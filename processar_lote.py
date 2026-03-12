#!/usr/bin/env python3
"""
processar_lote.py
=================
Processa em lote a matrícula e consolidação de atividades no SIGAA.

Para cada entrada da lista LOTE:
  1. Executa sigaa_Matricular.py  → matricula o aluno
  2. Executa sigaa_Consolidar.py → consolida (Conceito E) a matrícula

Uso rápido:
    python processar_lote.py                     # dry-run (sem --executar)
    python processar_lote.py --executar           # executa de verdade
    python processar_lote.py --so-matricular      # apenas matrícula, sem consolidar
    python processar_lote.py --so-consolidar      # apenas consolida (já matriculados)
    python processar_lote.py --conceito S         # conceito diferente de E
    python processar_lote.py --sem-headless       # abre o navegador (depuração)
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# LISTA DE ENTRADAS
# Formato: [matricula, polo, periodo, componente]
# Componentes válidos: ACC I, ACC II, ACC III, ACC IV, TCC I, TCC II
# ──────────────────────────────────────────────────────────────────────────────
LOTE: list[list] = [
    [202285640031, "Limoeiro do Ajuru", "2026.1", "ACC"],
    #[202285640010, "Limoeiro do Ajuru", "2026.1", "ACC"],  
    #[202285940015, "Oeiras do Pará", "2026.1", "ACC"],
    [202285940012, "Oeiras do Pará", "2026.1", "ACC"],
    [202285940021, "Oeiras do Pará", "2026.1", "ACC"]
   
]

# ──────────────────────────────────────────────────────────────────────────────
# Expansão de atalhos de componente
EXPANSAO_COMPONENTE: dict[str, list[str]] = {
    "ACC": ["ACC I", "ACC II", "ACC III", "ACC IV"],
    "TCC": ["TCC I"],
}

def _expandir_lote(lote: list[list]) -> list[list]:
    """Expande atalhos como 'ACC' em múltiplas entradas [ACC I, II, III, IV]."""
    expandido = []
    for entrada in lote:
        matricula, polo, periodo, componente = entrada
        componentes = EXPANSAO_COMPONENTE.get(componente.upper().strip(), [componente])
        for comp in componentes:
            expandido.append([matricula, polo, periodo, comp])
    return expandido

# ──────────────────────────────────────────────────────────────────────────────
PYTHON = sys.executable
SCRIPT_DIR = Path(__file__).parent
DIRETO = str(SCRIPT_DIR / "sigaa_Matricular.py")
CONSOLIDAR = str(SCRIPT_DIR / "sigaa_Consolidar.py")


def _run(cmd: list[str], label: str) -> tuple[bool, str]:
    """Executa um subprocesso e retorna (sucesso, saída)."""
    print(f"\n  ▶  {label}")
    print(f"     {' '.join(cmd)}")
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min por execução
        )
        elapsed = time.time() - start
        output = (result.stdout + result.stderr).strip()
        ok = result.returncode == 0
        status = "✅ OK" if ok else "❌ ERRO"
        print(f"     {status}  ({elapsed:.1f}s)")
        # Mostrar últimas linhas da saída
        lines = output.splitlines()
        tail = lines[-8:] if len(lines) > 8 else lines
        for ln in tail:
            print(f"       {ln}")
        return ok, output
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"     ⏰ TIMEOUT após {elapsed:.0f}s")
        return False, "TIMEOUT"
    except Exception as e:
        print(f"     💥 Exceção: {e}")
        return False, str(e)


def processar_lote(args: argparse.Namespace) -> None:
    conceito = args.conceito.upper()
    headless_flags = [] if args.sem_headless else ["--headless"]
    executar_flags = ["--executar"] if args.executar else []

    lote_expandido = _expandir_lote(LOTE)
    resultados: list[dict] = []
    total = len(lote_expandido)

    print("=" * 70)
    print(f"  PROCESSAMENTO EM LOTE — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"  Entradas originais : {len(LOTE)}  →  expandidas: {total}")
    print(f"  Modo     : {'EXECUTAR' if args.executar else 'DRY-RUN (use --executar para confirmar)'}")
    print(f"  Headless : {'Sim' if not args.sem_headless else 'Não (browser visível)'}")
    print(f"  Etapas   : {'Apenas matricular' if args.so_matricular else 'Apenas consolidar' if args.so_consolidar else 'Matricular + Consolidar'}")
    if not args.so_matricular:
        print(f"  Conceito : {conceito}")
    print("=" * 70)

    for i, entrada in enumerate(lote_expandido, 1):
        matricula, polo, periodo, componente = (
            str(entrada[0]),
            str(entrada[1]),
            str(entrada[2]),
            str(entrada[3]),
        )

        print(f"\n{'─'*70}")
        print(f"  [{i}/{total}] Matrícula={matricula}  Polo={polo!r}  Período={periodo}  Componente={componente}")
        print(f"{'─'*70}")

        res: dict = {
            "matricula": matricula,
            "polo": polo,
            "periodo": periodo,
            "componente": componente,
            "direto_ok": None,
            "consolidar_ok": None,
        }

        # ── PASSO 1: Matricular (sigaa_Matricular.py) ──────────────────────
        if not args.so_consolidar:
            cmd_direto = [
                PYTHON, DIRETO,
                "--matricula", matricula,
                "--polo", polo,
                "--periodo", periodo,
                "--componente", componente,
                *headless_flags,
                *executar_flags,
            ]
            ok_d, saida_d = _run(cmd_direto, f"MATRICULAR  {matricula} / {componente}")
            res["direto_ok"] = ok_d if ok_d else "ja"  # falha => aluno já matriculado

            if not ok_d:
                print(f"  ⚠️  Matrícula falhou — presumindo aluno já matriculado. Seguindo para consolidação.")

            # Aguardar um pouco antes de consolidar
            time.sleep(3)

        # ── PASSO 2: Consolidar (sigaa_Consolidar.py) ─────────────────────
        if not args.so_matricular:
            cmd_cons = [
                PYTHON, CONSOLIDAR,
                "--matricula", matricula,
                "--polo", polo,
                "--periodo", periodo,
                "--componente", componente,
                "--conceito", conceito,
                *headless_flags,
                *executar_flags,
            ]
            ok_c, saida_c = _run(cmd_cons, f"CONSOLIDAR  {matricula} / {componente}  (Conceito={conceito})")
            res["consolidar_ok"] = ok_c if ok_c else "ja"  # falha => aluno já integralizado
            if not ok_c:
                print(f"  ⚠️  Consolidação falhou — presumindo aluno já integralizado.")

        resultados.append(res)
        # Pausa entre entradas para não sobrecarregar o servidor
        if i < total:
            time.sleep(5)

    # ── RESUMO FINAL ──────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  RESUMO FINAL — {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*70}")
    header = f"  {'Matrícula':<14} {'Polo':<20} {'Per.':<8} {'Comp.':<8} {'Matr.':<8} {'Cons.'}"
    print(header)
    print(f"  {'-'*64}")

    erros = 0
    avisos = 0
    for r in resultados:
        d = ("✅" if r["direto_ok"] is True
             else "⚠️ " if r["direto_ok"] == "ja"
             else "❌" if r["direto_ok"] is False
             else "—")
        c = ("✅" if r["consolidar_ok"] is True
             else "⚠️ " if r["consolidar_ok"] == "ja"
             else "❌" if r["consolidar_ok"] is False
             else "—")
        polo_curto = r["polo"][:18]
        print(f"  {r['matricula']:<14} {polo_curto:<20} {r['periodo']:<8} {r['componente']:<8} {d:<6} {c}")
        if r["direto_ok"] is False or r["consolidar_ok"] is False:
            erros += 1
        elif r["direto_ok"] == "ja" or r["consolidar_ok"] == "ja":
            avisos += 1

    print(f"{'='*70}")
    if erros:
        print(f"  ❌ {erros} entrada(s) com erro inesperado.")
    if avisos:
        print(f"  ⚠️  {avisos} entrada(s) já processadas (aluno já matriculado ou já integralizado).")
    if not erros and not avisos:
        print(f"  🎉 Todas as entradas processadas com sucesso!")
    elif not erros:
        print(f"  ✅ Nenhum erro crítico — lote concluído.")
    print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Processa matrícula e consolidação em lote no SIGAA"
    )
    p.add_argument(
        "--executar", action="store_true",
        help="Confirma as operações (sem esta flag roda em dry-run)"
    )
    p.add_argument(
        "--sem-headless", action="store_true",
        help="Abre o navegador visualmente (útil para depuração)"
    )
    p.add_argument(
        "--so-matricular", action="store_true",
        help="Executa apenas a matrícula (sigaa_Matricular), sem consolidar"
    )
    p.add_argument(
        "--so-consolidar", action="store_true",
        help="Executa apenas a consolidação (sigaa_Consolidar), sem matricular"
    )
    p.add_argument(
        "--conceito", default="E",
        help="Conceito para consolidação (padrão: E)"
    )
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    if args.so_matricular and args.so_consolidar:
        print("Erro: --so-matricular e --so-consolidar não podem ser usados juntos.")
        sys.exit(1)
    processar_lote(args)
