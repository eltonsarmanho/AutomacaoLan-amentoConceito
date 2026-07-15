"""
lancamento_service.py — Serviço de backend para matrícula e consolidação no SIGAA.

Usado pelo frontend Streamlit. Por padrão roda em modo headless (sem janela).

Exemplos de uso (contexto async):

    # ACC
    svc = LancamentoService(
        matricula="202285940020",
        polo="OEIRAS DO PARÁ",
        periodo="2026.1",
        componente="ACC I",
    )
    resultado = await svc.matricular()
    resultado = await svc.consolidar(conceito="E")

    # TCC
    svc = LancamentoService(
        matricula="202416040009",
        polo="CAMETÁ",
        periodo="2026.2",
        componente="TCC I",
        orientador="ELTON SARMANHO SIQUEIRA",
    )
    resultado = await svc.matricular()
    resultado = await svc.consolidar(conceito="E")

    # Debug com janela visível
    svc = LancamentoService(..., headless=False)
"""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace


# ── Resultado ─────────────────────────────────────────────────────────────────


@dataclass
class ResultadoOperacao:
    sucesso: bool
    mensagem: str
    detalhes: list[str] = field(default_factory=list)


# ── Serviço ───────────────────────────────────────────────────────────────────


class LancamentoService:
    """
    Automatiza matrícula e consolidação no SIGAA via Playwright.

    Parâmetros
    ----------
    matricula  : Matrícula do aluno (ex: "202285940020")
    polo       : Nome do polo (ex: "OEIRAS DO PARÁ")
    periodo    : Período acadêmico (ex: "2026.1")
    componente : ACC I · ACC II · ACC III · ACC IV · TCC I · TCC II
    orientador : Obrigatório apenas para TCC
    executar   : False → dry-run (navega mas não confirma). Padrão True.
    headless   : False → abre janela do browser (útil para depuração). Padrão True.
    """

    COMPONENTES_VALIDOS = {"ACC I", "ACC II", "ACC III", "ACC IV", "TCC I", "TCC II"}

    def __init__(
        self,
        matricula: str,
        polo: str,
        periodo: str,
        componente: str,
        orientador: str | None = None,
        executar: bool = True,
        headless: bool = True,
    ) -> None:
        componente_upper = componente.strip().upper()
        if componente_upper not in self.COMPONENTES_VALIDOS:
            validos = ", ".join(sorted(self.COMPONENTES_VALIDOS))
            raise ValueError(
                f"Componente inválido: '{componente}'. Use um de: {validos}"
            )
        if componente_upper.startswith("TCC") and not orientador:
            raise ValueError(
                f"O campo 'orientador' é obrigatório para '{componente_upper}'."
            )

        self.matricula = matricula.strip()
        self.polo = polo.strip()
        self.periodo = periodo.strip()
        self.componente = componente_upper
        self.orientador = orientador
        self.executar = executar
        self.headless = headless

    # ── Builders de argumento ─────────────────────────────────────────────────

    def _args_matricular(self) -> SimpleNamespace:
        """Namespace esperado por sigaa_Matricular.executar_fluxo_direto
           e sigaa_Matricular_TCC.executar_fluxo_direto."""
        return SimpleNamespace(
            matricula=self.matricula,
            polo=self.polo,
            periodo=self.periodo,
            componente=self.componente,
            curso=None,           # usa polo para localizar curso no dropdown
            atividade_nome=None,  # usa nome padrão do MAPA_COMPONENTE
            orientador=self.orientador,
            executar=self.executar,
            headless=self.headless,
            manter_aberto=False,
        )

    def _args_consolidar(self, conceito: str) -> SimpleNamespace:
        """Namespace esperado por sigaa_Consolidar.executar_consolidacao
           e sigga_Consolidar_TCC.executar_consolidacao."""
        return SimpleNamespace(
            matricula=self.matricula,
            polo=self.polo,
            periodo=self.periodo,
            componente=self.componente,
            conceito=conceito.strip().upper(),
            curso=None,
            orientador=self.orientador,
            executar=self.executar,
            headless=self.headless,
            manter_aberto=False,
        )

    # ── Operações principais ──────────────────────────────────────────────────

    async def matricular(self) -> ResultadoOperacao:
        """
        Matrícula do aluno no componente via SIGAA.

        Fluxo (ACC):
          Login → Período → Portal Coord. Graduação → Curso/Polo →
          Atividades > Matricular → Buscar Discente →
          Tipo de Atividade → Buscar → Selecionar → Próximo Passo →
          Senha → Confirmar

        Fluxo (TCC):
          Mesmo fluxo, porém usa sigaa_Matricular_TCC. Etapa extra:
          Dados de Registro → Orientador (autocomplete) → Próximo Passo → Senha → Confirmar.
        """
        if self.componente.startswith("TCC"):
            from TCC.sigaa_Matricular_TCC import executar_fluxo_direto
        else:
            from ACC.sigaa_Matricular import executar_fluxo_direto

        args = self._args_matricular()
        try:
            await executar_fluxo_direto(args)
            acao = "simulada (dry-run)" if not self.executar else "concluída com sucesso"
            return ResultadoOperacao(
                sucesso=True,
                mensagem=(
                    f"Matrícula de {self.matricula} em '{self.componente}' "
                    f"(polo: {self.polo} | período: {self.periodo}) {acao}."
                ),
            )
        except Exception as exc:
            return ResultadoOperacao(
                sucesso=False,
                mensagem=f"Erro na matrícula: {exc}",
                detalhes=[repr(exc)],
            )

    async def consolidar(self, conceito: str = "E") -> ResultadoOperacao:
        """
        Consolida a matrícula atribuindo o conceito informado.

        Fluxo (ACC e TCC):
          Login → Período → Portal Coord. Graduação → Curso/Polo →
          Atividades > Consolidar Matrículas → Localizar Discente →
          Selecionar Conceito → Próximo Passo → Senha → Confirmar

        Parâmetros
        ----------
        conceito : Conceito a atribuir. Opções válidas: B, E, I, R, S. Padrão "E".
        """
        conceito_upper = conceito.strip().upper()
        conceitos_validos = {"B", "E", "I", "R", "S"}
        if conceito_upper not in conceitos_validos:
            raise ValueError(
                f"Conceito inválido: '{conceito}'. Use um de: {', '.join(sorted(conceitos_validos))}"
            )

        if self.componente.startswith("TCC"):
            from TCC.sigaa_Consolidar_TCC import executar_consolidacao
        else:
            from ACC.sigaa_Consolidar import executar_consolidacao

        args = self._args_consolidar(conceito_upper)
        try:
            await executar_consolidacao(args)
            acao = "simulada (dry-run)" if not self.executar else "concluída com sucesso"
            return ResultadoOperacao(
                sucesso=True,
                mensagem=(
                    f"Consolidação de {self.matricula} em '{self.componente}' "
                    f"(polo: {self.polo} | período: {self.periodo} "
                    f"| conceito: {conceito_upper}) {acao}."
                ),
            )
        except Exception as exc:
            return ResultadoOperacao(
                sucesso=False,
                mensagem=f"Erro na consolidação: {exc}",
                detalhes=[repr(exc)],
            )

    # ── Versões síncronas ─────────────────────────────────────────────────────

    def matricular_sync(self) -> ResultadoOperacao:
        """Versão síncrona de matricular(). Não use dentro de callbacks Streamlit."""
        return asyncio.run(self.matricular())

    def consolidar_sync(self, conceito: str = "E") -> ResultadoOperacao:
        """Versão síncrona de consolidar(). Não use dentro de callbacks Streamlit."""
        return asyncio.run(self.consolidar(conceito))
