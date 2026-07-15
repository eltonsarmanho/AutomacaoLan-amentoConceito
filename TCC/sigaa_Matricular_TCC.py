"""
sigaa_Matricular_TCC.py — Matricula um aluno em TCC I / TCC II no SIGAA.

Igual ao fluxo de ACC (ver sigaa_core.py), com uma etapa extra em
"Dados do Registro": preencher o Orientador via autocomplete AJAX
(/sigaa/ajaxDocente). O vínculo é VERIFICADO pelo hidden form:idOrientador —
se ficar vazio, o script tenta de novo e falha com mensagem clara em vez de
enviar um registro inválido.

Uso:
  python sigaa_Matricular_TCC.py --matricula 202416040009 --periodo 2026.3 \
      --polo "CAMETA" --componente "TCC I" --orientador "ELTON SARMANHO SIQUEIRA"

Exit codes: 0 = ok · 3 = já matriculado · 2 = erro real.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sigaa_core import entrada_de_args, executar_cli, fluxo_matricula, parser_base

COMPONENTES_ACEITOS = {"TCC I", "TCC II"}


async def executar_fluxo_direto(args) -> None:
    """API usada por lancamento_service.py e sigaa_lote.py."""
    entrada = entrada_de_args(args, exigir_orientador=True)
    if entrada.componente not in COMPONENTES_ACEITOS:
        raise ValueError(
            f"Este script atende apenas TCC I/II. Para ACC use sigaa_Matricular.py. "
            f"Recebido: {entrada.componente}"
        )
    await fluxo_matricula(entrada)


def main() -> None:
    parser = parser_base(
        "Matricula aluno em TCC I/II no SIGAA (com orientador)", com_orientador=True
    )
    args = parser.parse_args()
    executar_cli(executar_fluxo_direto(args))


if __name__ == "__main__":
    main()
