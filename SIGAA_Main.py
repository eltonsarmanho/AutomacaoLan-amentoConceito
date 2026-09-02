"""
SIGAA_Main.py — Script unificado (interativo) para Matrícula e Consolidação em lote no SIGAA.

Executa interativamente as etapas necessárias perguntando ao usuário:
  1. Lista de matrículas dos alunos
  2. Componente curricular (ACC, TCC, ESTAGIO)
  3. Período acadêmico e Polo
  4. Operação desejada (Matricular / Consolidar / Ambos)
  5. Conceito (apenas para consolidação — padrão E)
  6. Orientador (apenas para matrícula de TCC)

Desde a refatoração de 07/2026 este script NÃO chama mais subprocessos:
usa diretamente os fluxos de `sigaa_core.py` (menu determinístico via
jscook_action, matching estrito de componente, mensagens do SIGAA,
retentativas e rastreamento automático em rastreamento/).

Status possíveis por operação:
  ✓ ok  · ⚠ já processado (já matriculado/consolidado — não é erro crítico) · ✗ erro real

Uso:
  python SIGAA_Main.py                    # dry-run (para na etapa final)
  python SIGAA_Main.py --executar         # confirma operações no SIGAA
  python SIGAA_Main.py --headless         # sem interface gráfica
  python SIGAA_Main.py --sem-rastreio     # desliga screenshots/JSONL
"""

import argparse
import re
import sys

from sigaa_core import (
    ACC_COMPONENTE_UNICA,
    ACC_COMPONENTES_LEGADO,
    CONCEITOS_VALIDOS,
    Entrada,
    ItemLote,
    componentes_acc_para_matricula,
    executar_lote_sync,
)

# ── Constantes ─────────────────────────────────────────────────────────────────

POLOS = {
    "1": "CAMETA",
    "2": "LIMOEIRO DO AJURU",
    "3": "OEIRAS DO PARA",
}

SEPARADORES_MATRICULA = re.compile(r"[\s,;|/\\]+")

ICONES = {"ok": "✓", "ja": "⚠", "erro": "✗", "pulado": "–"}

# ── Helpers de entrada ─────────────────────────────────────────────────────────

def _linha(texto: str = "") -> None:
    print(texto)


def _titulo(texto: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {texto}")
    print(f"{'='*60}")


def _perguntar(prompt: str) -> str:
    """Lê uma linha do usuário com prompt, removendo espaços extras."""
    try:
        resposta = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[Interrompido pelo usuário]")
        sys.exit(0)
    return resposta


def _escolher_opcao(prompt: str, opcoes: dict[str, str]) -> str:
    """Exibe opções numeradas e retorna o valor escolhido."""
    _linha()
    print(prompt)
    for chave, valor in opcoes.items():
        print(f"  {chave}. {valor}")
    while True:
        resp = _perguntar("Sua escolha: ")
        if resp in opcoes:
            return opcoes[resp]
        print(f"  [!] Opção inválida. Digite um número entre: {', '.join(opcoes.keys())}")


# ── Coleta de dados ─────────────────────────────────────────────────────────────

def coletar_matriculas() -> list[str]:
    """Pergunta pela lista de matrículas e retorna lista validada."""
    _linha()
    print("Informe as matrículas dos alunos (separadas por vírgula, espaço ou quebra de linha).")
    print("Exemplo: 202285640002, 202285640001, 2024160040009")
    _linha()
    texto = _perguntar("Matrículas: ")
    partes = [p.strip() for p in SEPARADORES_MATRICULA.split(texto) if p.strip()]
    invalidas = [p for p in partes if not re.fullmatch(r"\d{10,15}", p)]
    if invalidas:
        print(f"  [AVISO] Valores que não parecem matrículas (continuando mesmo assim): {invalidas}")
    validas = [p for p in partes if re.fullmatch(r"\d{10,15}", p)]
    if not validas:
        print("  [ERRO] Nenhuma matrícula válida informada.")
        sys.exit(1)
    print(f"  {len(validas)} matrícula(s) reconhecida(s): {', '.join(validas)}")
    return validas


def coletar_componente() -> str:
    """Pergunta o componente curricular e retorna a sigla normalizada."""
    return _escolher_opcao(
        "Componente curricular:",
        {
            "1": "ACC",
            "2": "TCC",
            "3": "ESTAGIO",
        },
    )


def coletar_tcc_tipo() -> str:
    """Se TCC foi selecionado, pergunta qual tipo (TCC I ou TCC II)."""
    return _escolher_opcao(
        "Qual TCC?",
        {
            "1": "TCC I",
            "2": "TCC II",
        },
    )


def coletar_periodo() -> str:
    """Pergunta o período acadêmico no formato AAAA.N."""
    while True:
        periodo = _perguntar("\nPeríodo acadêmico (ex: 2026.3): ")
        if re.fullmatch(r"\d{4}\.\d", periodo):
            return periodo
        print("  [!] Formato inválido. Use AAAA.N (ex: 2026.3)")


def coletar_polo() -> str:
    """Pergunta o polo e retorna o nome completo."""
    return _escolher_opcao(
        "Polo:",
        POLOS,
    )


def coletar_operacao() -> str:
    """Pergunta a operação desejada."""
    return _escolher_opcao(
        "Operação a executar:",
        {
            "1": "MATRICULAR",
            "2": "CONSOLIDAR",
            "3": "MATRICULAR_E_CONSOLIDAR",
        },
    )


def coletar_conceito() -> str:
    """Pergunta o conceito para consolidação (Enter = E)."""
    _linha()
    while True:
        conceito = _perguntar(f"Conceito para consolidação [{'/'.join(sorted(CONCEITOS_VALIDOS))}] (Enter = E): ").upper()
        if not conceito:
            return "E"
        if conceito in CONCEITOS_VALIDOS:
            return conceito
        print(f"  [!] Conceito inválido. Use um de: {', '.join(sorted(CONCEITOS_VALIDOS))}")


def coletar_orientador(matriculas: list[str]) -> dict[str, str]:
    """Para TCC com matrícula, pede o orientador de cada aluno."""
    _linha()
    print("Para TCC, o orientador é obrigatório (nome exatamente como no SIGAA).")
    print("Se todos os alunos têm o mesmo orientador, informe uma vez.")
    print("Caso contrário, informe para cada matrícula individualmente.")
    _linha()

    mesmo = _perguntar("Todos têm o mesmo orientador? (s/n): ").lower()
    orientadores: dict[str, str] = {}

    if mesmo.startswith("s"):
        nome = _perguntar("Nome completo do orientador: ")
        for mat in matriculas:
            orientadores[mat] = nome
    else:
        for mat in matriculas:
            nome = _perguntar(f"Orientador para {mat}: ")
            orientadores[mat] = nome

    return orientadores


# ── Execução (direto no sigaa_core, sem subprocessos) ──────────────────────────

def _montar_entrada(args, matricula: str, periodo: str, polo: str, componente: str,
                    conceito: str = "E", orientador: str | None = None) -> Entrada:
    entrada = Entrada(
        matricula=matricula,
        periodo=periodo,
        polo=polo,
        componente=componente,
        conceito=conceito,
        orientador=orientador,
        executar=args.executar,
        headless=args.headless,
        rastrear=args.rastrear,
        tentativas=args.tentativas,
    )
    entrada.validar(exigir_orientador=orientador is not None)
    return entrada


def _montar_itens(args, matricula: str, periodo: str, polo: str, operacao: str,
                  componente: str, tcc_tipo: str | None, conceito: str,
                  orientador: str | None) -> list[ItemLote]:
    """Monta a fila de operações de UM aluno, na ordem correta e com dependências.

    A consolidação de um componente depende da matrícula dele: se a matrícula
    falhar, consolidar só produziria "nao esta entre as pendencias".
    """
    componentes = (componentes_acc_para_matricula(matricula) if componente == "ACC"
                   else (tcc_tipo,))
    matricular = operacao in ("MATRICULAR", "MATRICULAR_E_CONSOLIDAR")
    consolidar = operacao in ("CONSOLIDAR", "MATRICULAR_E_CONSOLIDAR")

    itens: list[ItemLote] = []
    for comp in componentes:
        if matricular:
            entrada = _montar_entrada(args, matricula, periodo, polo, comp,
                                      orientador=orientador if comp.startswith("TCC") else None)
            itens.append(ItemLote("matricular", entrada, f"Matricular {comp}"))
    for comp in componentes:
        if consolidar:
            entrada = _montar_entrada(args, matricula, periodo, polo, comp, conceito=conceito)
            dep = f"matricular:{matricula}:{comp}" if matricular else ""
            itens.append(ItemLote("consolidar", entrada,
                                  f"Consolidar {comp} (Conceito={conceito})", depende_de=dep))
    return itens


# ── Resumo final ───────────────────────────────────────────────────────────────

def _exibir_resumo(relatorio: list[dict]) -> None:
    _titulo("RESUMO FINAL")
    total = sum(len(r["detalhes"]) for r in relatorio)
    sucessos = sum(1 for r in relatorio for _, st in r["detalhes"] if st == "ok")
    avisos = sum(1 for r in relatorio for _, st in r["detalhes"] if st == "ja")
    falhas = sum(1 for r in relatorio for _, st in r["detalhes"] if st == "erro")
    pulados = sum(1 for r in relatorio for _, st in r["detalhes"] if st == "pulado")

    for entrada in relatorio:
        statuses = [st for _, st in entrada["detalhes"]]
        status_geral = ("OK" if all(st == "ok" for st in statuses)
                        else "ERRO" if any(st in ("erro", "pulado") for st in statuses)
                        else "OK (com avisos)")
        print(f"\n  Matrícula: {entrada['matricula']}  [{status_geral}]")
        for operacao, st in entrada["detalhes"]:
            print(f"    {ICONES[st]} {operacao}")

    _linha()
    print(f"  Total de operações: {total}  |  ✓ Sucesso: {sucessos}  |  "
          f"⚠ Já processado: {avisos}  |  ✗ Erro: {falhas}  |  – Pulado: {pulados}")
    if falhas > 0:
        print("  [AVISO] Há erros reais acima — screenshots e eventos em rastreamento/.")
    elif avisos > 0:
        print("  Nenhum erro crítico — avisos indicam operações já feitas anteriormente.")
    _linha()


# ── Ponto de entrada ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automação SIGAA em lote — matrícula e consolidação interativa"
    )
    parser.add_argument("--executar", action="store_true",
                        help="Confirma operações no SIGAA (sem este flag, modo dry-run)")
    parser.add_argument("--headless", action="store_true", help="Executa sem interface gráfica")
    parser.add_argument("--sem-rastreio", dest="rastrear", action="store_false",
                        help="Desliga o rastreamento (JSONL + screenshots em rastreamento/)")
    parser.add_argument("--tentativas", type=int, default=2,
                        help="Tentativas por operação em caso de falha real (padrão: 2)")
    args = parser.parse_args()

    _titulo("SIGAA — Automação em Lote")

    if not args.executar:
        print("  [MODO DRY-RUN] Use --executar para confirmar as operações no SIGAA.")

    # ── Coleta de parâmetros ────────────────────────────────────────────────────
    matriculas   = coletar_matriculas()
    componente   = coletar_componente()
    tcc_tipo     = None
    orientadores: dict[str, str] = {}

    if componente == "TCC":
        tcc_tipo = coletar_tcc_tipo()

    periodo  = coletar_periodo()
    polo     = coletar_polo()
    operacao = coletar_operacao()

    # Conceito só é necessário para consolidação
    conceito = "E"
    if operacao in ("CONSOLIDAR", "MATRICULAR_E_CONSOLIDAR"):
        conceito = coletar_conceito()

    # Orientador só é necessário para matrícula de TCC
    if componente == "TCC" and operacao in ("MATRICULAR", "MATRICULAR_E_CONSOLIDAR"):
        orientadores = coletar_orientador(matriculas)

    # ── Confirmação antes de executar ──────────────────────────────────────────
    _titulo("Confirme os dados")
    print(f"  Matrículas  : {', '.join(matriculas)}")
    if componente == "ACC":
        acc_resumo = (
            f"{ACC_COMPONENTE_UNICA} (SI05145 - ATIVIDADES COMPLEMENTARES) para ingressantes desde 2024; "
            f"{', '.join(ACC_COMPONENTES_LEGADO)} para ingressantes até 2023"
        )
        print(f"  Componente  : ACC → {acc_resumo}")
    else:
        print(f"  Componente  : {componente}" + (f" → {tcc_tipo}" if tcc_tipo else ""))
    print(f"  Período     : {periodo}")
    print(f"  Polo        : {polo}")
    print(f"  Operação    : {operacao}")
    if operacao in ("CONSOLIDAR", "MATRICULAR_E_CONSOLIDAR"):
        print(f"  Conceito    : {conceito}")
    print(f"  Modo        : {'EXECUTAR' if args.executar else 'DRY-RUN'}")
    print(f"  Rastreamento: {'ligado (rastreamento/)' if args.rastrear else 'desligado'}")
    _linha()

    confirmar = _perguntar("Prosseguir? (s/n): ").lower()
    if not confirmar.startswith("s"):
        print("Operação cancelada.")
        sys.exit(0)

    # ── Estágio: não implementado ──────────────────────────────────────────────
    if componente == "ESTAGIO":
        _titulo("Estágio — Não implementado")
        print("  Nenhum fluxo de matrícula/consolidação de Estágio está disponível no momento.")
        print("  O tipo existe no SIGAA (dropdown 'ESTÁGIO'); adapte sigaa_core.MAPA_COMPONENTE se necessário.")
        sys.exit(1)

    # ── Execução por aluno ─────────────────────────────────────────────────────
    relatorio: list[dict] = []

    for idx, matricula in enumerate(matriculas, start=1):
        _titulo(f"Aluno {idx}/{len(matriculas)} — {matricula}")
        itens = _montar_itens(args, matricula, periodo, polo, operacao, componente,
                              tcc_tipo, conceito, orientadores.get(matricula) or None)
        # Todas as operações do aluno rodam num único navegador/login.
        resultados = executar_lote_sync(itens)
        detalhes = [(r.item.rotulo, r.status) for r in resultados]
        relatorio.append({"matricula": matricula, "detalhes": detalhes})

    # ── Resumo ─────────────────────────────────────────────────────────────────
    _exibir_resumo(relatorio)


if __name__ == "__main__":
    main()
