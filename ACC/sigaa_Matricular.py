"""
sigaa_Matricular.py — Matricula um aluno em Atividade Complementar (ACC I..IV) no SIGAA.

Toda a lógica de navegação vive em sigaa_core.py (compartilhada com os demais
scripts). Fluxo: Login → Período → Portal Coord. Graduação → Curso/Polo →
Atividades > Matricular (via jscook_action determinístico) → Buscar Discente →
Tipo de Atividade → Selecionar Atividade → Próximo Passo → Senha → Confirmar.

Uso:
  # Dry-run (para na etapa final, sem confirmar)
  python sigaa_Matricular.py --matricula 202285640002 --periodo 2026.3 \
      --polo "LIMOEIRO DO AJURU" --componente "ACC I"

  # Executar de verdade
  python sigaa_Matricular.py ... --executar --headless

Exit codes: 0 = ok · 3 = já matriculado (não é erro crítico) · 2 = erro real.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sigaa_core import entrada_de_args, executar_cli, fluxo_matricula, parser_base

COMPONENTES_ACEITOS = {"ACC I", "ACC II", "ACC III", "ACC IV"}


async def executar_fluxo_direto(args) -> None:
    """API usada por lancamento_service.py e processar_lote.py."""
    entrada = entrada_de_args(args)
    if entrada.componente not in COMPONENTES_ACEITOS:
        raise ValueError(
            f"Este script atende apenas ACC (I..IV). Para TCC use sigaa_Matricular_TCC.py. "
            f"Recebido: {entrada.componente}"
        )
    await fluxo_matricula(entrada)


def main() -> None:
    parser = parser_base("Matricula aluno em Atividade Complementar (ACC) no SIGAA")
    args = parser.parse_args()
    executar_cli(executar_fluxo_direto(args))


if __name__ == "__main__":
    main()
