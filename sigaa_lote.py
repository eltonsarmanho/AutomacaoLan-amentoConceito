"""
sigaa_lote.py — Script unificado para Matrícula e Consolidação em lote no SIGAA.

Executa interativamente as etapas necessárias perguntando ao usuário:
  1. Lista de matrículas dos alunos
  2. Componente curricular (ACC, TCC, ESTAGIO)
  3. Período acadêmico e Polo
  4. Operação desejada (Matricular / Consolidar / Ambos)

Chamadas delegadas aos scripts existentes:
  - sigaa_Matricular.py      → Matrícula ACC I/II/III/IV
  - sigaa_Matricular_TCC.py  → Matrícula TCC I / TCC II
  - sigaa_Consolidar.py      → Consolidação ACC I/II/III/IV e TCC I/II
  - sigga_Consolidar_TCC.py  → Consolidação TCC I / TCC II (alternativo)

Uso:
  python sigaa_lote.py
  python sigaa_lote.py --executar        # confirma operações no SIGAA
  python sigaa_lote.py --headless        # sem interface gráfica
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ── Constantes ─────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent

SCRIPT_MATRICULAR_ACC = SCRIPT_DIR / "sigaa_Matricular.py"
SCRIPT_MATRICULAR_TCC = SCRIPT_DIR / "sigaa_Matricular_TCC.py"
SCRIPT_CONSOLIDAR     = SCRIPT_DIR / "sigaa_Consolidar.py"
SCRIPT_CONSOLIDAR_TCC = SCRIPT_DIR / "sigga_Consolidar_TCC.py"

POLOS = {
    "1": "CAMETA",
    "2": "LIMOEIRO DO AJURU",
    "3": "OEIRAS DO PARA",
}

ACC_COMPONENTES = ["ACC I", "ACC II", "ACC III", "ACC IV"]

SEPARADORES_MATRICULA = re.compile(r"[\s,;|/\\]+")

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
        periodo = _perguntar("\nPeríodo acadêmico (ex: 2026.2): ")
        if re.fullmatch(r"\d{4}\.\d", periodo):
            return periodo
        print("  [!] Formato inválido. Use AAAA.N (ex: 2026.2)")


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


def coletar_orientador(matriculas: list[str]) -> dict[str, str]:
    """Para TCC com matrícula, pede o orientador de cada aluno."""
    _linha()
    print("Para TCC, o orientador é obrigatório.")
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


# ── Execução dos subprocessos ──────────────────────────────────────────────────

def _executar_script(descricao: str, script: Path, args_extra: list[str], executar: bool, headless: bool) -> bool:
    """Chama um script Python como subprocesso. Retorna True se sucesso."""
    cmd = [sys.executable, str(script)] + args_extra
    if executar:
        cmd.append("--executar")
    if headless:
        cmd.append("--headless")

    print(f"\n  → {descricao}")
    print(f"    Comando: {' '.join(cmd)}")
    print(f"    {'─'*50}")

    try:
        resultado = subprocess.run(cmd, check=False)
        if resultado.returncode == 0:
            print(f"    [OK] Concluído com sucesso.")
            return True
        else:
            print(f"    [ERRO] Script encerrou com código {resultado.returncode}.")
            return False
    except FileNotFoundError:
        print(f"    [ERRO] Script não encontrado: {script}")
        return False
    except Exception as exc:
        print(f"    [ERRO] Falha inesperada: {exc}")
        return False


def matricular_acc(matricula: str, periodo: str, polo: str, executar: bool, headless: bool) -> list[tuple[str, bool]]:
    """Executa matrícula em todos os componentes ACC (I a IV) para um aluno."""
    resultados = []
    for comp in ACC_COMPONENTES:
        ok = _executar_script(
            descricao=f"Matricular {matricula} em {comp}",
            script=SCRIPT_MATRICULAR_ACC,
            args_extra=[
                "--matricula", matricula,
                "--periodo", periodo,
                "--polo", polo,
                "--componente", comp,
            ],
            executar=executar,
            headless=headless,
        )
        resultados.append((comp, ok))
    return resultados


def matricular_tcc(matricula: str, periodo: str, polo: str, componente: str, orientador: str, executar: bool, headless: bool) -> bool:
    """Executa matrícula em TCC I ou TCC II para um aluno."""
    return _executar_script(
        descricao=f"Matricular {matricula} em {componente}",
        script=SCRIPT_MATRICULAR_TCC,
        args_extra=[
            "--matricula", matricula,
            "--periodo", periodo,
            "--polo", polo,
            "--componente", componente,
            "--orientador", orientador,
        ],
        executar=executar,
        headless=headless,
    )


def consolidar_acc(matricula: str, periodo: str, polo: str, executar: bool, headless: bool) -> list[tuple[str, bool]]:
    """Executa consolidação em todos os componentes ACC (I a IV) para um aluno."""
    resultados = []
    for comp in ACC_COMPONENTES:
        ok = _executar_script(
            descricao=f"Consolidar {matricula} em {comp}",
            script=SCRIPT_CONSOLIDAR,
            args_extra=[
                "--matricula", matricula,
                "--periodo", periodo,
                "--polo", polo,
                "--componente", comp,
            ],
            executar=executar,
            headless=headless,
        )
        resultados.append((comp, ok))
    return resultados


def consolidar_tcc(matricula: str, periodo: str, polo: str, componente: str, executar: bool, headless: bool) -> bool:
    """Executa consolidação de TCC I ou TCC II para um aluno."""
    return _executar_script(
        descricao=f"Consolidar {matricula} em {componente}",
        script=SCRIPT_CONSOLIDAR_TCC,
        args_extra=[
            "--matricula", matricula,
            "--periodo", periodo,
            "--polo", polo,
            "--componente", componente,
        ],
        executar=executar,
        headless=headless,
    )


# ── Resumo final ───────────────────────────────────────────────────────────────

def _exibir_resumo(relatorio: list[dict]) -> None:
    _titulo("RESUMO FINAL")
    total = sum(len(r["detalhes"]) for r in relatorio)
    sucessos = sum(1 for r in relatorio for _, ok in r["detalhes"] if ok)
    falhas = total - sucessos

    for entrada in relatorio:
        status_geral = "OK" if all(ok for _, ok in entrada["detalhes"]) else "PARCIAL/ERRO"
        print(f"\n  Matrícula: {entrada['matricula']}  [{status_geral}]")
        for operacao, ok in entrada["detalhes"]:
            icone = "✓" if ok else "✗"
            print(f"    {icone} {operacao}")

    _linha()
    print(f"  Total de operações: {total}  |  Sucesso: {sucessos}  |  Falhas: {falhas}")
    if falhas > 0:
        print("  [AVISO] Verifique as entradas com erro acima.")
    _linha()


# ── Ponto de entrada ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automação SIGAA em lote — matrícula e consolidação interativa"
    )
    parser.add_argument("--executar", action="store_true", help="Confirma operações no SIGAA (sem este flag, modo dry-run)")
    parser.add_argument("--headless", action="store_true", help="Executa sem interface gráfica")
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

    # Orientador só é necessário para matrícula de TCC
    if componente == "TCC" and operacao in ("MATRICULAR", "MATRICULAR_E_CONSOLIDAR"):
        orientadores = coletar_orientador(matriculas)

    # ── Confirmação antes de executar ──────────────────────────────────────────
    _titulo("Confirme os dados")
    print(f"  Matrículas  : {', '.join(matriculas)}")
    print(f"  Componente  : {componente}" + (f" → {tcc_tipo}" if tcc_tipo else " → ACC I, ACC II, ACC III, ACC IV" if componente == "ACC" else ""))
    print(f"  Período     : {periodo}")
    print(f"  Polo        : {polo}")
    print(f"  Operação    : {operacao}")
    print(f"  Modo        : {'EXECUTAR' if args.executar else 'DRY-RUN'}")
    _linha()

    confirmar = _perguntar("Prosseguir? (s/n): ").lower()
    if not confirmar.startswith("s"):
        print("Operação cancelada.")
        sys.exit(0)

    # ── Estágio: não implementado ──────────────────────────────────────────────
    if componente == "ESTAGIO":
        _titulo("Estágio — Não implementado")
        print("  Nenhum script de matrícula/consolidação de Estágio está disponível no momento.")
        print("  Verifique se existe um script específico no diretório e adapte este arquivo.")
        sys.exit(1)

    # ── Execução por aluno ─────────────────────────────────────────────────────
    relatorio: list[dict] = []

    for idx, matricula in enumerate(matriculas, start=1):
        _titulo(f"Aluno {idx}/{len(matriculas)} — {matricula}")
        detalhes: list[tuple[str, bool]] = []

        # ── MATRÍCULA ──────────────────────────────────────────────────────────
        if operacao in ("MATRICULAR", "MATRICULAR_E_CONSOLIDAR"):
            if componente == "ACC":
                for comp, ok in matricular_acc(matricula, periodo, polo, args.executar, args.headless):
                    detalhes.append((f"Matricular {comp}", ok))
            elif componente == "TCC":
                orientador = orientadores.get(matricula, "")
                ok = matricular_tcc(matricula, periodo, polo, tcc_tipo, orientador, args.executar, args.headless)
                detalhes.append((f"Matricular {tcc_tipo}", ok))

        # ── CONSOLIDAÇÃO ───────────────────────────────────────────────────────
        if operacao in ("CONSOLIDAR", "MATRICULAR_E_CONSOLIDAR"):
            if componente == "ACC":
                for comp, ok in consolidar_acc(matricula, periodo, polo, args.executar, args.headless):
                    detalhes.append((f"Consolidar {comp}", ok))
            elif componente == "TCC":
                ok = consolidar_tcc(matricula, periodo, polo, tcc_tipo, args.executar, args.headless)
                detalhes.append((f"Consolidar {tcc_tipo}", ok))

        relatorio.append({"matricula": matricula, "detalhes": detalhes})

    # ── Resumo ─────────────────────────────────────────────────────────────────
    _exibir_resumo(relatorio)


if __name__ == "__main__":
    main()
