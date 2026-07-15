"""
sigaa_Consolidar.py — Consolida (lança conceito) matrícula em ACC ou TCC no SIGAA.

Fluxo (ver sigaa_core.py): Login → Período → Portal Coord. Graduação →
Curso/Polo → Atividades > Consolidar Matrículas (jscook_action determinístico) →
localizar matrícula SOB o componente exato → Conceito → Próximo Passo →
Senha (se pedida) → Confirmar.

Correções em relação à versão anterior:
  - O mapa de componentes agora inclui TCC I/II (antes dava KeyError).
  - "ACC I" nunca casa com "ACC II/III/IV" (matching estrito).
  - Mensagens do SIGAA ("já consolidada", "integralizado") viram exit code 3.

Uso:
  python sigaa_Consolidar.py --matricula 202285640002 --periodo 2026.3 \
      --polo "LIMOEIRO DO AJURU" --componente "ACC I"            # dry-run
  python sigaa_Consolidar.py ... --conceito E --executar --headless

Exit codes: 0 = ok · 3 = já consolidado/integralizado · 2 = erro real.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sigaa_core import entrada_de_args, executar_cli, fluxo_consolidacao, parser_base


async def executar_consolidacao(args) -> None:
    """API usada por lancamento_service.py e processar_lote.py (ACC e TCC)."""
    entrada = entrada_de_args(args)
    await fluxo_consolidacao(entrada)


def main() -> None:
    parser = parser_base(
        "Consolida matricula em ACC/TCC no SIGAA", com_conceito=True
    )
    args = parser.parse_args()
    executar_cli(executar_consolidacao(args))


if __name__ == "__main__":
    main()
