"""
sigaa_Consolidar_TCC.py — Consolida matrícula de TCC I / TCC II no SIGAA.

Mesmo fluxo de sigaa_Consolidar.py (núcleo em sigaa_core.py); este script
apenas restringe os componentes a TCC. Correção importante em relação à
versão anterior (sigga_Consolidar_TCC.py): a etapa final agora preenche a
SENHA quando o SIGAA a exige — antes o script clicava Confirmar sem senha
e a consolidação falhava silenciosamente.

Uso:
  python sigaa_Consolidar_TCC.py --matricula 202416040009 --periodo 2026.3 \
      --polo "CAMETA" --componente "TCC I"                       # dry-run
  python sigaa_Consolidar_TCC.py ... --executar --headless

Exit codes: 0 = ok · 3 = já consolidado · 2 = erro real.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sigaa_core import entrada_de_args, executar_cli, fluxo_consolidacao, parser_base

COMPONENTES_ACEITOS = {"TCC I", "TCC II"}


async def executar_consolidacao(args) -> None:
    """API usada por lancamento_service.py e sigaa_lote.py."""
    entrada = entrada_de_args(args)
    if entrada.componente not in COMPONENTES_ACEITOS:
        raise ValueError(
            f"Este script atende apenas TCC I/II. Para ACC use sigaa_Consolidar.py. "
            f"Recebido: {entrada.componente}"
        )
    await fluxo_consolidacao(entrada)


def main() -> None:
    parser = parser_base(
        "Consolida matricula de TCC I/II no SIGAA", com_conceito=True
    )
    args = parser.parse_args()
    executar_cli(executar_consolidacao(args))


if __name__ == "__main__":
    main()
