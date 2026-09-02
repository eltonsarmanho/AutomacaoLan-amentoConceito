"""
sigaa_core.py — Núcleo compartilhado da automação SIGAA (matrícula e consolidação).

Este módulo concentra TODA a lógica de navegação que antes estava duplicada em
sigaa_Matricular.py, sigaa_Matricular_TCC.py, sigaa_Consolidar.py e
sigga_Consolidar_TCC.py. Os quatro scripts agora são apenas CLIs finos.

Principais garantias de robustez (baseadas no rastreamento real do SIGAA —
mapeamento_acc_*.jsonl e mapeamento_tcc_*.jsonl):

1. MENU DETERMINÍSTICO — o menu "Atividades" (JSCookMenu) é acionado por um
   POST com campo oculto `jscook_action` (ex.:
   `menu_coordenador_..._menu:A]#{ registroAtividade.iniciarMatricula}`).
   Extraímos a ação diretamente dos <script> da página e submetemos o form,
   sem depender de hover de mouse (que era a principal fonte de falhas).
   O hover real permanece como fallback.

2. MATCHING ESTRITO DE COMPONENTE — "TCC I" nunca casa com "TCC II";
   "ACC I" nunca casa com "ACC II/III/IV" (regex com lookahead de numeral romano).

3. MENSAGENS DO SIGAA — os painéis de erro/aviso/info do SIGAA são lidos e
   classificados: "já matriculado"/"já consolidada" viram JaProcessadoError
   (exit code 3), diferente de falha real (exit code 2).

4. RASTREAMENTO EMBUTIDO — cada execução grava rastreamento/<tag>_<ts>/ com
   eventos JSONL (POSTs ao SIGAA, navegações, etapas) e screenshot por etapa.

5. VERIFICAÇÃO DE ETAPA FINAL — o dry-run só declara sucesso se a página de
   confirmação (senha + botão Confirmar) estiver realmente presente.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# ── Constantes ─────────────────────────────────────────────────────────────────

TIPO_ACC = "ATIVIDADES COMPLEMENTARES"
TIPO_TCC = "TRABALHO DE CONCLUSÃO DE CURSO"

ANO_INICIO_ACC_UNICA = 2024
ACC_COMPONENTE_UNICA = "ACC"
ACC_COMPONENTES_LEGADO = ("ACC I", "ACC II", "ACC III", "ACC IV")

# sigla → (tipo de atividade no dropdown, nome da atividade na tabela/na lista de consolidação)
MAPA_COMPONENTE = {
    "ACC":     (TIPO_ACC, "SI05145 - ATIVIDADES COMPLEMENTARES"),
    "ACC I":   (TIPO_ACC, "SI05051 - ATIVIDADES CURRICULARES COMPLEMENTARES I"),
    "ACC II":  (TIPO_ACC, "SI05052 - ATIVIDADES CURRICULARES COMPLEMENTARES II"),
    "ACC III": (TIPO_ACC, "SI05053 - ATIVIDADES CURRICULARES COMPLEMENTARES III"),
    "ACC IV":  (TIPO_ACC, "SI05054 - ATIVIDADES COMPLEMENTARES IV"),
    "TCC I":   (TIPO_TCC, "TRABALHO DE CONCLUSAO DE CURSO I"),
    "TCC II":  (TIPO_TCC, "TRABALHO DE CONCLUSAO DE CURSO II"),
}

CONCEITOS_VALIDOS = {"B", "E", "I", "R", "S"}


def componentes_acc_para_matricula(matricula: str) -> tuple[str, ...]:
    """Retorna os componentes ACC válidos conforme o ano inicial da matrícula.

    Ingressantes a partir de 2024 usam exclusivamente SI05145. Matrículas
    anteriores mantêm a matriz com ACC I a IV.
    """
    try:
        ano_inicial = int(matricula.strip()[:4])
    except (TypeError, ValueError):
        raise ValueError(f"Não foi possível identificar o ano da matrícula: {matricula!r}")
    return (ACC_COMPONENTE_UNICA,) if ano_inicial >= ANO_INICIO_ACC_UNICA else ACC_COMPONENTES_LEGADO

# Textos (normalizados) que indicam "não é erro crítico, apenas já foi feito"
PADROES_JA_PROCESSADO = [
    "ja se encontra matriculado",
    "ja esta matriculado",
    "ja possui matricula",
    "ja foi consolidada",
    "ja consolidada",
    "ja se encontra consolidad",
    "matricula ja consolidada",
    "integralizou",
    "integralizado",
    "ja cumpriu",
]

PADROES_SUCESSO = [
    "sucesso",
    "realizada com sucesso",
    "consolidada com sucesso",
]

RASTREAMENTO_DIR = Path(__file__).parent / "rastreamento"


# ── Erros tipados ──────────────────────────────────────────────────────────────

class ConfigError(RuntimeError):
    """Configuração inválida (.env incompleto etc.)."""


class FluxoError(RuntimeError):
    """Falha real de navegação/execução no SIGAA."""

    def __init__(self, mensagem: str, mensagens_sigaa: list[str] | None = None):
        super().__init__(mensagem)
        self.mensagens_sigaa = mensagens_sigaa or []


class AtividadeNaoPendenteError(FluxoError):
    """A atividade pedida não está na lista de pendências do discente.

    Duas causas possíveis e indistinguíveis nesta tela: já foi consolidada, ou o
    aluno nunca foi matriculado nela. Quem chama (o lote) sabe qual das duas é,
    porque acompanhou a etapa de matrícula.
    """


class JaProcessadoError(FluxoError):
    """O SIGAA indicou que a operação já foi feita (já matriculado/consolidado)."""


# ── Config / helpers puros ─────────────────────────────────────────────────────

@dataclass
class ConfigSigaa:
    login: str
    senha: str
    sigaa_url: str


def ler_config_env() -> ConfigSigaa:
    load_dotenv()
    login = os.getenv("LOGIN")
    senha = os.getenv("SENHA")
    sigaa_url = os.getenv("SIGAA_URL")
    faltando = [n for n, v in {"LOGIN": login, "SENHA": senha, "SIGAA_URL": sigaa_url}.items() if not v]
    if faltando:
        raise ConfigError(f"Variaveis obrigatorias faltando no .env: {', '.join(faltando)}")
    return ConfigSigaa(login=login, senha=senha, sigaa_url=sigaa_url)


def norm(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    ascii_only = base.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def variacoes_periodo(periodo: str) -> list[str]:
    base = periodo.strip()
    return list(dict.fromkeys([base, base.replace(".", "-"), base.replace("-", ".")]))


def base_sigaa_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def contem_componente_exato(texto: str, nome_componente: str) -> bool:
    """True se `texto` contém `nome_componente` como componente EXATO.

    Impede que "TCC I" case com "TCC II" e que o nome-base ("TRABALHO DE
    CONCLUSAO DE CURSO") case com as variantes I/II. Acentos e caixa são
    ignorados.
    """
    t = norm(texto)
    n = norm(nome_componente)
    if not n:
        return False
    # (?![a-z0-9])  → o nome não pode continuar (ex.: "...CURSO I" dentro de "...CURSO II")
    # (?!\s+[ivx]+(?![a-z0-9])) → não pode vir um numeral romano isolado depois
    #                             (ex.: nome-base seguido de " I")
    pat = re.compile(re.escape(n) + r"(?![a-z0-9])(?!\s+[ivx]+(?![a-z0-9]))")
    return bool(pat.search(t))


# "SI05145 - ATIVIDADES COMPLEMENTARES" → código "SI05145" + nome-base
RE_CODIGO_COMPONENTE = re.compile(r"^\s*([A-Za-z]{2,5}\s*\d{3,6})\s*-\s*")
# qualquer código de componente solto dentro de um texto de linha/célula
RE_CODIGO_SOLTO = re.compile(r"\b[A-Za-z]{2,5}\d{3,6}\b")


def separar_codigo_componente(nome_componente: str) -> tuple[str, str]:
    """Divide 'SI05145 - ATIVIDADES COMPLEMENTARES' em ('SI05145', 'ATIVIDADES ...').

    Sem código (ex.: 'TRABALHO DE CONCLUSAO DE CURSO I') devolve ('', nome).
    """
    nome = (nome_componente or "").strip()
    m = RE_CODIGO_COMPONENTE.match(nome)
    if not m:
        return "", nome
    return re.sub(r"\s+", "", m.group(1)).upper(), nome[m.end():].strip()


def componente_casa(texto: str, nome_componente: str) -> bool:
    """True se `texto` designa o MESMO componente que `nome_componente`.

    A auditoria das telas do SIGAA mostrou que o mesmo componente aparece com e
    sem o código conforme a tela:

      • lista "Consolidar Matrículas" → cabeçalho de grupo SEM código
        ("ATIVIDADES COMPLEMENTARES")
      • busca/seleção de atividade    → COM código
        ("SI05145 - ATIVIDADES COMPLEMENTARES - 150h")

    Por isso quem decide o casamento é o nome-base (com a mesma proteção de
    numeral romano de `contem_componente_exato`). O código só é exigido quando
    ele aparece nos DOIS lados — assim 'SI05145 - ATIVIDADES COMPLEMENTARES'
    nunca casa com uma linha 'SI05054 - ...'.
    """
    codigo, base = separar_codigo_componente(nome_componente)
    if not contem_componente_exato(texto, base):
        return False
    if not codigo:
        return True
    codigos_no_texto = {c.upper() for c in RE_CODIGO_SOLTO.findall(texto or "")}
    return not codigos_no_texto or codigo in codigos_no_texto


def classificar_mensagens(mensagens: list[str]) -> str:
    """Retorna 'ja_processado', 'sucesso', 'erro' ou 'nenhuma'."""
    corpo = norm(" | ".join(mensagens))
    if not corpo:
        return "nenhuma"
    if any(p in corpo for p in PADROES_JA_PROCESSADO):
        return "ja_processado"
    if any(p in corpo for p in PADROES_SUCESSO):
        return "sucesso"
    return "erro"


# ── Rastreador (mapeamento automático de cada execução) ────────────────────────

class Rastreador:
    """Grava eventos JSONL + screenshots de cada etapa em rastreamento/<tag>_<ts>/."""

    def __init__(self, tag: str, ativo: bool = True):
        self.ativo = ativo
        self.eventos: list[dict] = []
        self._n_shot = 0
        self._handlers = None
        if ativo:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.dir = RASTREAMENTO_DIR / f"{tag}_{ts}"
            self.dir.mkdir(parents=True, exist_ok=True)
            self.jsonl = self.dir / "eventos.jsonl"
        else:
            self.dir = None
            self.jsonl = None

    def evento(self, tipo: str, **dados) -> None:
        if not self.ativo:
            return
        ev = {"ts": datetime.now().isoformat(), "tipo": tipo, **dados}
        self.eventos.append(ev)
        try:
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def anexar_pagina(self, page) -> None:
        """Registra POSTs relevantes ao SIGAA e navegações de página."""
        if not self.ativo:
            return

        def _on_request(req):
            try:
                if req.method != "POST":
                    return
                url = req.url
                if any(x in url for x in ["/a4j/", "javax.faces.resource", ".png", ".gif", ".css", ".js"]):
                    return
                body = req.post_data or ""
                body = re.sub(r"(senha|password)=[^&]*", r"\1=***", body)
                body = re.sub(r"javax\.faces\.ViewState=[^&]*", "ViewState=...", body)
                self.evento("post", url=url, corpo=body[:800])
            except Exception:
                pass

        def _on_nav(frame):
            try:
                if frame.parent_frame is None:
                    self.evento("pagina", url=frame.url)
            except Exception:
                pass

        page.on("request", _on_request)
        page.on("framenavigated", _on_nav)
        self._handlers = (page, _on_request, _on_nav)

    def desanexar_pagina(self) -> None:
        """Remove os listeners — evita acumulo quando a sessao e reaproveitada."""
        if not self._handlers:
            return
        page, on_request, on_nav = self._handlers
        for evento, fn in (("request", on_request), ("framenavigated", on_nav)):
            try:
                page.remove_listener(evento, fn)
            except Exception:
                pass
        self._handlers = None

    async def screenshot(self, page, etapa: str) -> None:
        if not self.ativo:
            return
        self._n_shot += 1
        nome = re.sub(r"[^a-z0-9_-]+", "_", norm(etapa))[:60]
        try:
            await page.screenshot(path=str(self.dir / f"{self._n_shot:02d}_{nome}.png"), full_page=False)
        except Exception:
            pass

    async def etapa(self, page, nome: str, detalhe: str = "") -> None:
        print(f"[{nome}]" + (f" {detalhe}" if detalhe else ""))
        self.evento("etapa", nome=nome, detalhe=detalhe, url=getattr(page, "url", None))
        if page is not None:
            await self.screenshot(page, nome)


# ── Entradas ───────────────────────────────────────────────────────────────────

@dataclass
class Entrada:
    matricula: str
    periodo: str
    polo: str
    componente: str                 # sigla: ACC I..IV, TCC I, TCC II
    conceito: str = "E"             # só para consolidação
    orientador: str | None = None   # só para matrícula de TCC
    curso: str | None = None        # sobrescreve polo na seleção de curso
    atividade_nome: str | None = None
    executar: bool = False
    headless: bool = False
    manter_aberto: bool = False
    rastrear: bool = True
    tentativas: int = 2

    def validar(self, exigir_orientador: bool = False) -> None:
        comp = self.componente.strip().upper()
        if comp not in MAPA_COMPONENTE:
            raise ValueError(
                f"Componente invalido: {self.componente}. Use: {', '.join(sorted(MAPA_COMPONENTE))}"
            )
        self.componente = comp
        self.conceito = (self.conceito or "E").strip().upper()
        if self.conceito not in CONCEITOS_VALIDOS:
            raise ValueError(f"Conceito invalido: {self.conceito}. Use: {', '.join(sorted(CONCEITOS_VALIDOS))}")
        if exigir_orientador and comp.startswith("TCC") and not (self.orientador or "").strip():
            raise ValueError(f"Orientador e obrigatorio para matricula de {comp}.")
        if not re.fullmatch(r"\d{6,15}", self.matricula.strip()):
            raise ValueError(f"Matricula invalida: {self.matricula!r}")
        self.matricula = self.matricula.strip()


def entrada_de_args(args, exigir_orientador: bool = False) -> Entrada:
    """Converte argparse.Namespace/SimpleNamespace (compatível com scripts antigos)."""
    e = Entrada(
        matricula=str(getattr(args, "matricula")),
        periodo=str(getattr(args, "periodo")),
        polo=str(getattr(args, "polo")),
        componente=str(getattr(args, "componente")),
        conceito=str(getattr(args, "conceito", "E") or "E"),
        orientador=getattr(args, "orientador", None),
        curso=getattr(args, "curso", None),
        atividade_nome=getattr(args, "atividade_nome", None),
        executar=bool(getattr(args, "executar", False)),
        headless=bool(getattr(args, "headless", False)),
        manter_aberto=bool(getattr(args, "manter_aberto", False)),
        rastrear=bool(getattr(args, "rastrear", True)),
        tentativas=int(getattr(args, "tentativas", 2) or 1),
    )
    e.validar(exigir_orientador=exigir_orientador)
    return e


# ── Sessão SIGAA ───────────────────────────────────────────────────────────────

class SessaoSigaa:
    """Encapsula uma sessão Playwright logada no SIGAA com etapas reutilizáveis."""

    def __init__(self, page, cfg: ConfigSigaa, rast: Rastreador):
        self.page = page
        self.cfg = cfg
        self.rast = rast
        # preenchido por selecionar_periodo(); usado para desempatar linhas
        # do mesmo componente em períodos diferentes na lista de consolidação
        self.periodo: str = ""
        # curso já aplicado nesta sessão — evita reselecionar a cada operação
        self._curso_ativo: str = ""

    # ---- utilitários --------------------------------------------------------

    async def _esperar_pagina(self, timeout_ms: int = 10000) -> None:
        for estado in ("domcontentloaded", "networkidle"):
            try:
                await self.page.wait_for_load_state(estado, timeout=timeout_ms)
            except Exception:
                pass
        await self.page.wait_for_timeout(400)

    async def mensagens_sigaa(self) -> list[str]:
        """Extrai as mensagens dos painéis padrão do SIGAA (erros/avisos/info)."""
        try:
            msgs = await self._evaluate("""() => {
                const sels = ['ul.erros li', '.erros li', '.erro li', 'ul.info li',
                              '.info li', 'ul.aviso li', '.aviso li', '.mensagens li',
                              '#painel-erros li', 'li.error', '.msgErro'];
                const out = [];
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach(el => {
                        const t = el.textContent.trim();
                        if (t && !out.includes(t)) out.push(t);
                    });
                }
                return out;
            }""")
            if msgs:
                self.rast.evento("mensagens_sigaa", mensagens=msgs)
            return msgs
        except Exception:
            return []

    async def _checar_ja_processado(self, contexto: str) -> None:
        msgs = await self.mensagens_sigaa()
        if classificar_mensagens(msgs) == "ja_processado":
            raise JaProcessadoError(
                f"{contexto}: operacao ja realizada segundo o SIGAA — {' | '.join(msgs)}",
                mensagens_sigaa=msgs,
            )

    async def _corpo(self) -> str:
        try:
            return await self.page.locator("body").inner_text()
        except Exception:
            return ""

    async def _screenshot_falha(self, nome: str) -> None:
        try:
            if self.rast.ativo:
                await self.page.screenshot(path=str(self.rast.dir / f"FALHA_{nome}.png"), full_page=True)
                print(f"   [DEBUG] Screenshot de falha: {self.rast.dir}/FALHA_{nome}.png")
        except Exception:
            pass

    async def _goto_resiliente(self, url: str, tentativas: int = 3) -> None:
        """`page.goto` com retentativa e timeout crescente.

        O SIGAA responde em segundos na maior parte do tempo, mas a auditoria de
        02/09/2026 mostrou 7 falhas de `Page.goto: Timeout 20000ms` só na tela de
        login — sempre transitórias. Retentar aqui evita queimar uma tentativa
        inteira do fluxo (que reabre o navegador do zero) por lentidão do servidor.
        """
        ultima: Exception | None = None
        for i in range(1, tentativas + 1):
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=20000 + 15000 * i)
                return
            except Exception as e:
                ultima = e
                self.rast.evento("goto_retry", url=url, tentativa=i, erro=str(e)[:160])
                print(f"   [WARN] Navegacao para {url} falhou (tentativa {i}/{tentativas}); repetindo...")
                await self.page.wait_for_timeout(1500 * i)
        raise FluxoError(f"Nao foi possivel abrir {url} apos {tentativas} tentativas: {ultima}")

    async def _evaluate(self, js: str, *args):
        """`page.evaluate` tolerante a navegação em curso.

        O SIGAA responde muitos cliques com POST+redirect; avaliar JS nesse
        intervalo estoura "Execution context was destroyed". Esperar a página
        assentar e repetir resolve — sem isso o fluxo perdia a tentativa inteira.
        """
        ultimo: Exception | None = None
        for tentativa in range(3):
            try:
                return await self.page.evaluate(js, *args)
            except Exception as e:
                if "Execution context was destroyed" not in str(e) and "navigating" not in str(e):
                    raise
                ultimo = e
                await self._esperar_pagina(timeout_ms=8000)
        raise ultimo

    async def _esperar_url(self, fragmento: str, timeout_ms: int = 12000) -> bool:
        """Aguarda a URL conter `fragmento`.

        Depois de clicar numa seta o SIGAA responde com um POST + redirect; o
        `expect_navigation` às vezes resolve no passo intermediário e a checagem
        de URL feita uma única vez dava falso negativo (a matrícula de ACC IV
        falhou assim em 02/09/2026, mesmo já estando em dados_registro.jsf).
        """
        limite = time.monotonic() + timeout_ms / 1000
        while True:
            if fragmento in self.page.url:
                return True
            if time.monotonic() >= limite:
                return fragmento in self.page.url
            await self.page.wait_for_timeout(500)

    async def _pagina_vazia(self) -> bool:
        """True quando o SIGAA devolve uma resposta sem conteúdo útil.

        Acontece esporadicamente: a página carrega só com o cabeçalho (sem menu,
        sem formulário). Sem isso o fluxo interpreta como 'tela errada' e aborta.
        """
        try:
            return await self._evaluate("""() => {
                const txt = (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').trim();
                const temForm = !!document.querySelector('form input, form select, table.listagem');
                return txt.length < 400 && !temForm;
            }""")
        except Exception:
            return False

    async def _recuperar_pagina_vazia(self, contexto: str) -> bool:
        """Recarrega a página quando ela veio em branco. True se recuperou."""
        if not await self._pagina_vazia():
            return False
        print(f"   [WARN] Pagina em branco em '{contexto}'; recarregando...")
        self.rast.evento("pagina_vazia", contexto=contexto, url=self.page.url)
        for _ in range(2):
            try:
                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await self._esperar_pagina()
            if not await self._pagina_vazia():
                print("   [OK] Pagina recarregada com conteudo.")
                return True
        return False

    async def _sessao_expirada(self) -> bool:
        """True se o SIGAA devolveu a tela de login / sessão encerrada."""
        if "verTelaLogin" in self.page.url or "logar.do" in self.page.url:
            return True
        corpo = norm(await self._corpo())
        return "sessao expirou" in corpo or "sua sessao expirou" in corpo

    # ---- etapas comuns ------------------------------------------------------

    async def login(self) -> None:
        await self.rast.etapa(None, "login", "Abrindo SIGAA e autenticando")
        await self._goto_resiliente(self.cfg.sigaa_url)

        campo_login = self.page.locator("input[name='user.login']").first
        await campo_login.wait_for(state="visible", timeout=15000)
        await campo_login.fill(self.cfg.login)
        await self.page.locator("input[name='user.senha']").first.fill(self.cfg.senha)
        await self.page.locator("input[type='submit']").first.click()
        await self._esperar_pagina()

        corpo = norm(await self._corpo())
        if "usuario e/ou senha" in corpo or "usuario ou senha" in corpo:
            raise FluxoError("Login rejeitado pelo SIGAA: usuario e/ou senha invalidos.")
        if "verTelaLogin" in self.page.url and "logar" not in self.page.url:
            raise FluxoError(f"Login nao avancou. URL atual: {self.page.url}")
        await self.rast.screenshot(self.page, "apos_login")

    async def selecionar_periodo(self, periodo: str) -> None:
        """Best-effort: clica no link do período no calendário pós-login.

        O rastreamento real mostrou que o Portal do Coordenador abre mesmo sem
        clicar no período, então a ausência do link NÃO é erro fatal.
        """
        self.periodo = periodo
        await self.rast.etapa(self.page, "periodo", f"Selecionando periodo {periodo} (best-effort)")
        for variacao in variacoes_periodo(periodo):
            try:
                link = self.page.locator(f"a:has-text('{variacao}')").first
                await link.wait_for(state="visible", timeout=2500)
                await link.click()
                await self._esperar_pagina()
                print(f"   [OK] Periodo '{variacao}' selecionado.")
                return
            except Exception:
                continue
        print(f"   [INFO] Link do periodo '{periodo}' nao encontrado; seguindo direto ao portal.")

    async def abrir_portal_coordenador(self) -> None:
        await self.rast.etapa(self.page, "portal", "Abrindo Portal Coord. Graduacao")
        if "coordenador.jsf" in self.page.url:
            return
        base = base_sigaa_url(self.cfg.sigaa_url)
        try:
            link = self.page.locator("a[href*='verPortalCoordenadorGraduacao']").first
            await link.wait_for(state="visible", timeout=4000)
            await link.click()
            await self._esperar_pagina()
        except Exception:
            await self._goto_resiliente(f"{base}/sigaa/verPortalCoordenadorGraduacao.do")
            await self._esperar_pagina()
        await self._recuperar_pagina_vazia("portal")
        if "coordenador.jsf" not in self.page.url:
            await self._screenshot_falha("portal")
            raise FluxoError(f"Nao abriu Portal Coord. Graduacao. URL: {self.page.url}")

    async def selecionar_curso(self, polo_ou_curso: str) -> None:
        """Seleciona o curso no dropdown do portal e VERIFICA que ele foi aplicado."""
        await self.rast.etapa(self.page, "curso", f"Selecionando curso por '{polo_ou_curso}'")
        alvo = norm(polo_ou_curso)

        for tentativa in range(3):
            # O portal às vezes pinta antes de o dropdown de curso ter opções.
            # Sem esta espera o fluxo abortava com "polo nao encontrado" (1 falha
            # observada em 02/09/2026) mesmo com o polo correto.
            await self._esperar_dropdown_de_curso()
            selecionou = await self._evaluate("""(alvo) => {
                function norm(s) {
                    return (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                }
                for (const sel of document.querySelectorAll('select')) {
                    for (const opt of sel.options) {
                        if (norm(opt.text).includes(alvo) && !norm(opt.text).includes('mudar de curso')) {
                            const texto = opt.text.trim();
                            if (sel.value === opt.value) {
                                return {status: 'ja-selecionado', curso: texto};
                            }
                            sel.value = opt.value;
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            // dropdown do SIGAA tem onchange="submit()"; garante o submit
                            const form = sel.closest('form');
                            if (form) form.submit();
                            return {status: 'selecionado', curso: texto};
                        }
                    }
                }
                return null;
            }""", alvo)

            if not selecionou:
                if tentativa < 2:
                    print(f"   [WARN] Tentativa {tentativa+1}: polo '{polo_ou_curso}' ainda nao esta "
                          "no dropdown; recarregando o portal...")
                    await self.page.reload(wait_until="domcontentloaded")
                    await self._esperar_pagina()
                    continue
                await self._screenshot_falha("curso")
                opcoes = await self._opcoes_de_curso()
                raise FluxoError(
                    f"Curso/polo '{polo_ou_curso}' nao encontrado no dropdown do portal. "
                    f"Opcoes disponiveis: {opcoes if opcoes else '(dropdown vazio)'}"
                )
            self.rast.evento("curso", resultado=selecionou)
            await self._esperar_pagina(timeout_ms=15000)
            await self.page.wait_for_timeout(1200)  # JS do menu ThemeOffice inicializar

            # Valida pelo NOME COMPLETO do curso (ex.: "SISTEMAS DE INFORMACAO/CCAME - CAMETÁ").
            # Usar só o polo daria falso positivo: o cabeçalho do campus sempre contém "CAMETA".
            curso_completo = norm(selecionou["curso"])
            corpo = norm(await self._corpo())
            if curso_completo and curso_completo in corpo:
                print(f"   [OK] Curso ativo confere: '{selecionou['curso']}'.")
                await self.rast.screenshot(self.page, "curso_ok")
                return
            print(f"   [WARN] Tentativa {tentativa+1}: curso '{selecionou['curso']}' ainda nao aparece "
                  "como ativo; repetindo selecao...")

        await self._screenshot_falha("curso_nao_aplicado")
        raise FluxoError(f"Selecao de curso '{polo_ou_curso}' nao foi aplicada pelo SIGAA.")

    async def _opcoes_de_curso(self) -> list[str]:
        """Rótulos do dropdown de curso — usado só para mensagens de erro úteis."""
        try:
            return await self._evaluate("""() => {
                for (const sel of document.querySelectorAll('select')) {
                    const opts = [...sel.options].map(o => o.text.trim()).filter(Boolean);
                    if (opts.some(o => /-\\s*\\w/.test(o)) && opts.length > 1) return opts.slice(0, 12);
                }
                return [];
            }""")
        except Exception:
            return []

    async def _esperar_dropdown_de_curso(self, timeout_ms: int = 8000) -> None:
        """Aguarda o dropdown do portal ter mais de uma opção (ele chega vazio às vezes)."""
        alvo = self.page.wait_for_function(
            """() => [...document.querySelectorAll('select')].some(s => s.options.length > 1)""",
            timeout=timeout_ms,
        )
        try:
            await alvo
        except Exception:
            pass

    async def _voltar_ao_portal(self) -> None:
        """Recarrega o Portal do Coordenador (mantendo o curso já selecionado)."""
        base = base_sigaa_url(self.cfg.sigaa_url)
        await self._goto_resiliente(f"{base}/sigaa/verPortalCoordenadorGraduacao.do")
        await self._esperar_pagina()
        await self._recuperar_pagina_vazia("voltar_portal")
        # o JS do ThemeOffice precisa rodar antes de o menu aceitar o submit
        await self.page.wait_for_timeout(1200)

    # ---- menu Atividades (determinístico via jscook_action) ------------------

    async def _extrair_acoes_menu(self) -> dict:
        """Lê os <script> do portal e devolve {label_normalizada: (form, jscook_action)}.

        Formato real dos itens do JSCookMenu no SIGAA (confirmado no portal):
            ['<img .../>', 'Consolidar Matr&#237;culas',
             'menu_coordenador_..._menu:A]#{ registroAtividade.iniciarConsolidarMatriculas}',
             'menu_coordenador', null]
        A string de ação JÁ inclui o prefixo do menu; o 4º campo é o id do form.
        """
        dados = await self._evaluate(r"""() => {
            const itens = [];
            const ta = document.createElement('textarea');
            const decode = (s) => { ta.innerHTML = s; return ta.value; };
            for (const s of document.querySelectorAll('script')) {
                const src = s.textContent || '';
                if (!src.includes("A]#{")) continue;
                const re = /'([^'<>]{2,100})'\s*,\s*'([^']*A\]#\{[^']*\})'\s*,\s*'([^']*)'/g;
                let m;
                while ((m = re.exec(src)) !== null) {
                    itens.push({label: decode(m[1]), action: m[2], form: m[3]});
                }
            }
            return itens;
        }""")
        mapa = {}
        for item in dados:
            label = norm(item["label"])
            if label and item["action"] and label not in mapa:
                mapa[label] = (item.get("form") or "", item["action"])
        self.rast.evento("menu_acoes", total=len(mapa), labels=sorted(mapa.keys())[:60])
        return mapa

    async def _submeter_jscook_action(self, form_id: str, action: str) -> bool:
        url_antes = self.page.url
        try:
            resultado = await self._evaluate("""(params) => {
                const {formId, action} = params;
                let form = formId ? document.getElementById(formId) : null;
                if (!form) {
                    // fallback: form cujo id é prefixo da ação
                    for (const f of document.querySelectorAll('form')) {
                        if (f.id && action.startsWith(f.id)) { form = f; break; }
                    }
                }
                if (!form) form = document.getElementById('menu_coordenador');
                if (!form) form = document.querySelector('form');
                if (!form) return 'sem-form';
                let inp = form.querySelector("input[name='jscook_action']");
                if (!inp) {
                    inp = document.createElement('input');
                    inp.type = 'hidden';
                    inp.name = 'jscook_action';
                    form.appendChild(inp);
                }
                inp.value = action;
                form.submit();
                return 'submetido[' + form.id + ']:' + inp.value;
            }""", {"formId": form_id, "action": action})
            self.rast.evento("jscook_submit", resultado=resultado)
            if resultado == "sem-form":
                return False
        except Exception as e:
            self.rast.evento("jscook_submit", erro=str(e)[:200])
        try:
            await self.page.wait_for_url(lambda url: url != url_antes, timeout=12000)
        except Exception:
            pass
        await self._esperar_pagina()
        return True

    async def _menu_hover_fallback(self, rotulo_parcial: str) -> bool:
        """Fallback antigo: hover real no JSCookMenu (mantido por segurança)."""
        try:
            ativ_box = await self._evaluate("""() => {
                for (const td of document.querySelectorAll('td')) {
                    if ((td.className.includes('ThemeOfficeMainItem'))
                        && /Atividades/i.test(td.textContent.trim())) {
                        const r = td.getBoundingClientRect();
                        if (r.width > 0) return {x: r.x + r.width/2, y: r.y + r.height/2, bottom: r.bottom};
                    }
                }
                return null;
            }""")
        except Exception:
            ativ_box = None
        if not ativ_box:
            return False

        for tentativa in range(3):
            await self.page.mouse.move(ativ_box["x"], ativ_box["y"])
            await self.page.wait_for_timeout(800 + tentativa * 300)
            alvo = await self._evaluate("""(partial) => {
                function norm(s) {
                    return (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                }
                for (const tr of document.querySelectorAll('tr.ThemeOfficeMenuItem')) {
                    if (norm(tr.textContent).includes(norm(partial))) {
                        const r = tr.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.top > 0) {
                            return {x: r.x + r.width/2, y: r.y + r.height/2};
                        }
                    }
                }
                return null;
            }""", rotulo_parcial)
            if not alvo:
                await self.page.mouse.move(0, 0)
                await self.page.wait_for_timeout(400)
                continue
            mid_y = ativ_box.get("bottom", ativ_box["y"] + 15) + 5
            await self.page.mouse.move(ativ_box["x"], mid_y, steps=3)
            await self.page.mouse.move(alvo["x"], mid_y, steps=3)
            await self.page.mouse.move(alvo["x"], alvo["y"], steps=5)
            await self.page.wait_for_timeout(200)
            try:
                async with self.page.expect_navigation(timeout=8000):
                    await self.page.mouse.click(alvo["x"], alvo["y"])
            except Exception:
                pass
            await self._esperar_pagina()
            return True
        return False

    async def menu_atividades(self, rotulo: str, urls_esperadas: list[str], textos_esperados: list[str]) -> None:
        """Navega Atividades > <rotulo> — 1º via jscook_action, 2º via hover."""
        await self.rast.etapa(self.page, "menu", f"Atividades > {rotulo}")

        async def _chegou(espera_s: float = 0) -> bool:
            """Confere a chegada; opcionalmente insiste por alguns segundos.

            A tela de consolidação fica na MESMA URL do portal (coordenador.jsf),
            então a checagem depende do texto — que pode demorar a pintar. Uma
            checagem única gerava falso negativo (falha observada em 02/09/2026).
            """
            limite = time.monotonic() + espera_s
            while True:
                if any(u in self.page.url for u in urls_esperadas):
                    return True
                corpo = norm(await self._corpo())
                if any(norm(t) in corpo for t in textos_esperados):
                    return True
                if time.monotonic() >= limite:
                    return False
                await self.page.wait_for_timeout(700)

        # Estratégia 1: jscook_action determinístico
        acoes = await self._extrair_acoes_menu()
        alvo_norm = norm(rotulo)
        # match exato primeiro — evita que 'Matricular' case com 'Matricular Aluno Ativo'
        candidato = None
        if alvo_norm in acoes:
            candidato = (alvo_norm, *acoes[alvo_norm])
        else:
            for label, (form_id, action) in acoes.items():
                if alvo_norm in label or label in alvo_norm:
                    candidato = (label, form_id, action)
                    break
        if candidato:
            label, form_id, action = candidato
            print(f"   [OK] Acao de menu encontrada nos scripts: '{label}' → {action}")
            # 2 tentativas: o submit do JSCookMenu pode ser engolido quando o
            # portal ainda esta reinicializando o menu apos a troca de curso.
            for tentativa in range(2):
                await self._submeter_jscook_action(form_id, action)
                await self._recuperar_pagina_vazia(f"menu_{rotulo}")
                if await _chegou(espera_s=6):
                    print(f"   [OK] Navegou via jscook_action. URL={self.page.url}")
                    await self.rast.screenshot(self.page, f"menu_{rotulo}_ok")
                    return
                if tentativa == 0:
                    print(f"   [WARN] Menu '{rotulo}' nao abriu; recarregando o portal e repetindo...")
                    await self._voltar_ao_portal()
                    # os ids do form do menu mudam a cada render — reextrai a ação
                    acoes = await self._extrair_acoes_menu()
                    if label in acoes:
                        form_id, action = acoes[label]
            print("   [WARN] jscook_action nao levou a pagina esperada; tentando hover...")
        else:
            print(f"   [WARN] Rotulo '{rotulo}' nao encontrado nos scripts do menu; tentando hover...")

        # Estratégia 2: hover real (fallback)
        if await self._menu_hover_fallback(rotulo) and await _chegou():
            print(f"   [OK] Navegou via hover. URL={self.page.url}")
            await self.rast.screenshot(self.page, f"menu_{rotulo}_ok")
            return

        await self._screenshot_falha(f"menu_{norm(rotulo).replace(' ', '_')}")
        msgs = await self.mensagens_sigaa()
        raise FluxoError(
            f"Nao foi possivel navegar Atividades > {rotulo}. URL atual: {self.page.url}",
            mensagens_sigaa=msgs,
        )

    # ---- matrícula -----------------------------------------------------------

    async def buscar_discente(self, matricula: str) -> None:
        await self.rast.etapa(self.page, "buscar_discente", f"Buscando matricula {matricula}")
        check = self.page.locator("[id='formulario:checkMatricula']").first
        try:
            await check.wait_for(state="visible", timeout=8000)
            if not await check.is_checked():
                await check.check(force=True)
        except Exception:
            pass  # alguns temas usam radio já marcado

        campo = self.page.locator("[id='formulario:matriculaDiscente']").first
        await campo.wait_for(state="visible", timeout=8000)
        await campo.fill(matricula)

        await self.page.locator("[id='formulario:buscar']").first.click()
        await self._esperar_pagina()
        await self._checar_ja_processado("Busca de discente")

        corpo = await self._corpo()
        if matricula not in corpo:
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("busca_discente")
            raise FluxoError(
                f"Aluno {matricula} nao apareceu no resultado da busca. "
                f"Mensagens SIGAA: {' | '.join(msgs) if msgs else '(nenhuma)'}",
                mensagens_sigaa=msgs,
            )

    async def selecionar_discente(self, matricula: str) -> None:
        """Clica na seta (input name='form:selecionarDiscente') na linha da matrícula."""
        await self.rast.etapa(self.page, "selecionar_discente", f"Selecionando discente {matricula}")
        marcado = await self._evaluate("""(matricula) => {
            for (const tr of document.querySelectorAll('tr')) {
                if (!tr.textContent.includes(matricula)) continue;
                const seta = tr.querySelector("input[name='form:selecionarDiscente']")
                          || tr.querySelector('input[type=image]')
                          || tr.querySelector("a[onclick*='jsfcljs']");
                if (seta) {
                    seta.setAttribute('data-sigaa-alvo', '1');
                    return true;
                }
            }
            return false;
        }""", matricula)
        if not marcado:
            await self._screenshot_falha("selecionar_discente")
            raise FluxoError(f"Nao encontrou a seta de selecao na linha da matricula {matricula}.")

        url_antes = self.page.url
        try:
            async with self.page.expect_navigation(timeout=15000):
                await self.page.locator("[data-sigaa-alvo='1']").first.click()
        except Exception:
            if self.page.url == url_antes:
                await self.page.wait_for_timeout(2500)
        await self._esperar_pagina()
        await self._checar_ja_processado("Selecao de discente")

        chegou = False
        limite = time.monotonic() + 10
        while True:
            chegou = await self._evaluate("""() => {
                return !!(document.querySelector("[id='form:idTipoAtividade']")
                       || document.querySelector("[id='form:atividades']"));
            }""")
            if chegou or "busca_atividade" in self.page.url or time.monotonic() >= limite:
                break
            await self.page.wait_for_timeout(500)
        if not chegou and "busca_atividade" not in self.page.url:
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("apos_selecionar_discente")
            raise FluxoError(
                f"Apos selecionar o discente, a pagina de busca de atividade nao abriu. URL: {self.page.url}",
                mensagens_sigaa=msgs,
            )

    async def selecionar_atividade(self, tipo_atividade: str, atividade_nome: str) -> None:
        """Filtra por tipo, clica na atividade e garante a chegada a dados_registro.jsf.

        Retentavel: quando o SIGAA devolve a resposta do clique em branco ou
        quebrada, refazemos a busca em vez de abortar o fluxo inteiro.
        """
        ultima: Exception | None = None
        for tentativa in range(1, 3):
            try:
                await self._selecionar_atividade_uma_vez(tipo_atividade, atividade_nome)
                return
            except JaProcessadoError:
                raise
            except FluxoError as e:
                ultima = e
                if tentativa == 2 or not await self._voltar_a_busca_de_atividade():
                    raise
                print(f"   [WARN] Selecao de atividade falhou ({e}); refazendo a busca...")
        raise ultima  # pragma: no cover - defensivo

    async def _voltar_a_busca_de_atividade(self) -> bool:
        """Volta a tela de busca de atividade do discente ja selecionado."""
        for _ in range(2):
            try:
                await self.page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await self._esperar_pagina()
            if await self._sessao_expirada():
                return False
            pronto = await self._evaluate(
                """() => !!document.querySelector("[id='form:idTipoAtividade']")"""
            )
            if pronto:
                return True
        return False

    async def _selecionar_atividade_uma_vez(self, tipo_atividade: str, atividade_nome: str) -> None:
        await self.rast.etapa(self.page, "atividade", f"Tipo '{tipo_atividade}' → '{atividade_nome}'")

        # 1. radio "Tipo de Atividade" (obrigatório — sem ele o SIGAA ignora o filtro)
        radio = self.page.locator("[id='form:tipoAtividade']").first
        try:
            await radio.wait_for(state="visible", timeout=8000)
            await radio.check(force=True)
        except Exception:
            try:
                await self.page.locator("[name='form:tipoAtividade']").first.check(force=True)
            except Exception:
                print("   [WARN] Radio form:tipoAtividade nao encontrado; seguindo.")

        # 2. dropdown do tipo
        sel = self.page.locator("[id='form:idTipoAtividade']").first
        await sel.wait_for(state="visible", timeout=8000)
        opcoes = await sel.locator("option").all_text_contents()
        alvo = norm(tipo_atividade)
        rotulo = next((o for o in opcoes if alvo in norm(o)), None)
        if not rotulo:
            raise FluxoError(f"Tipo de atividade '{tipo_atividade}' nao existe no dropdown: {opcoes}")
        await sel.select_option(label=rotulo.strip())

        # 3. Buscar Atividades
        await self.page.locator("[id='form:atividades']").first.click()
        await self._esperar_pagina()

        # 4. localizar a linha EXATA da atividade e clicar na seta
        for tentativa in range(2):
            linhas = await self._evaluate("""() => {
                const out = [];
                document.querySelectorAll('table tr').forEach((tr, i) => {
                    const temSeta = !!tr.querySelector('input[type=image]');
                    if (temSeta) out.push({idx: i, texto: tr.textContent.trim().substring(0, 200)});
                });
                return out;
            }""")
            alvo_linha = next(
                (l for l in linhas if componente_casa(l["texto"], atividade_nome)), None
            )
            if alvo_linha is not None:
                await self._evaluate("""(idx) => {
                    const tr = document.querySelectorAll('table tr')[idx];
                    const seta = tr.querySelector('input[type=image]');
                    seta.setAttribute('data-sigaa-alvo-ativ', '1');
                }""", alvo_linha["idx"])
                url_antes = self.page.url
                try:
                    async with self.page.expect_navigation(timeout=15000):
                        await self.page.locator("[data-sigaa-alvo-ativ='1']").first.click()
                except Exception:
                    if self.page.url == url_antes:
                        await self.page.wait_for_timeout(2500)
                await self._esperar_pagina()
                await self._checar_ja_processado("Selecao de atividade")
                break

            if tentativa == 0:
                # refinar busca pelo nome
                print(f"   [INFO] '{atividade_nome}' nao visivel; refinando busca pelo nome...")
                try:
                    campo_nome = self.page.locator("[id='form:nomeAtividadeInput']").first
                    if await campo_nome.count():
                        await campo_nome.fill(atividade_nome)
                        await self.page.locator("[id='form:atividades']").first.click()
                        await self._esperar_pagina()
                        continue
                except Exception:
                    pass
            textos = [l["texto"][:80] for l in linhas[:10]]
            await self._screenshot_falha("atividade")
            raise FluxoError(
                f"Atividade '{atividade_nome}' nao encontrada na tabela. Linhas visiveis: {textos}"
            )

        # 5. garantir chegada a dados_registro.jsf
        # O redirect pode demorar; e às vezes a resposta vem em branco.
        if not await self._esperar_url("dados_registro.jsf"):
            await self._recuperar_pagina_vazia("apos_selecionar_atividade")
            await self._esperar_url("dados_registro.jsf", timeout_ms=5000)
        if "dados_registro.jsf" not in self.page.url:
            for seletor in [
                "input[type='submit'][value*='Próximo']:not([id*='btnAtividades'])",
                "input[type='submit'][value*='Proximo']:not([id*='btnAtividades'])",
                "input[type='submit'][value*='Confirmar']:not([id*='btnAtividades'])",
            ]:
                try:
                    loc = self.page.locator(seletor).first
                    await loc.wait_for(state="visible", timeout=3000)
                    await loc.click()
                    await self._esperar_pagina()
                    break
                except Exception:
                    continue
        if "dados_registro.jsf" not in self.page.url:
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("dados_registro")
            raise FluxoError(
                f"Nao chegou a dados_registro.jsf apos selecionar a atividade. URL: {self.page.url}",
                mensagens_sigaa=msgs,
            )

    async def preencher_orientador(self, orientador: str) -> None:
        """Preenche o autocomplete de orientador e VERIFICA form:idOrientador."""
        await self.rast.etapa(self.page, "orientador", f"Preenchendo orientador '{orientador}'")
        campo = self.page.locator("input[name='orientador.nome']").first
        if not await campo.count():
            campo = self.page.locator("input[id='paramAjaxDocente_1']").first
        await campo.wait_for(state="visible", timeout=8000)

        for tentativa in range(2):
            await campo.click()
            await campo.fill("")
            # keyboard.type dispara keydown reais — necessário para o AJAX /sigaa/ajaxDocente
            await self.page.keyboard.type(orientador, delay=40)
            await self.page.wait_for_timeout(2000)
            await self.page.keyboard.press("Tab")
            await self.page.wait_for_timeout(1000)

            id_orientador = await self._evaluate("""() => {
                const el = document.querySelector("input[id='form:idOrientador'], input[name='form:idOrientador']");
                return el ? el.value : '';
            }""")
            if id_orientador:
                print(f"   [OK] Orientador vinculado (form:idOrientador={id_orientador}).")
                self.rast.evento("orientador", id=id_orientador)
                return
            print(f"   [WARN] Tentativa {tentativa+1}: autocomplete nao vinculou o orientador; repetindo...")

        # última chance: clicar na sugestão visível
        try:
            sugestao = self.page.locator(f"div:visible >> text=/{re.escape(orientador.split()[0])}/i").first
            await sugestao.click(timeout=3000)
            await self.page.wait_for_timeout(800)
        except Exception:
            pass
        id_orientador = await self._evaluate("""() => {
            const el = document.querySelector("input[id='form:idOrientador'], input[name='form:idOrientador']");
            return el ? el.value : '';
        }""")
        if not id_orientador:
            await self._screenshot_falha("orientador")
            raise FluxoError(
                f"Nao foi possivel vincular o orientador '{orientador}' (form:idOrientador vazio). "
                "Confira se o nome esta exatamente como no SIGAA."
            )

    async def proximo_passo(self) -> None:
        await self.rast.etapa(self.page, "proximo_passo", "Clicando 'Proximo Passo >>'")
        clicou = False
        for seletor in [
            "input[id='form:btnConfirmacao']",
            "input[id*='btnConfirmacao']",
            "input[type='submit'][value*='Próximo Passo']",
            "input[type='submit'][value*='Proximo Passo']",
            "input[type='submit'][value*='ximo']",
        ]:
            try:
                loc = self.page.locator(seletor).first
                await loc.wait_for(state="visible", timeout=4000)
                await loc.click()
                clicou = True
                break
            except Exception:
                continue
        if not clicou:
            try:
                await self._evaluate(
                    "() => { const b = document.getElementById('form:btnConfirmacao'); if (b) b.click(); }"
                )
                clicou = True
            except Exception:
                pass
        if not clicou:
            await self._screenshot_falha("proximo_passo")
            raise FluxoError("Botao 'Proximo Passo' nao encontrado.")
        await self._esperar_pagina()

        # Validações do SIGAA podem barrar aqui (ex.: orientador obrigatório)
        msgs = await self.mensagens_sigaa()
        classe = classificar_mensagens(msgs)
        if classe == "ja_processado":
            raise JaProcessadoError(f"Ja processado: {' | '.join(msgs)}", mensagens_sigaa=msgs)
        if classe == "erro" and not await self._tem_confirmacao_final():
            await self._screenshot_falha("validacao_proximo_passo")
            raise FluxoError(f"SIGAA barrou o Proximo Passo: {' | '.join(msgs)}", mensagens_sigaa=msgs)

    async def _tem_confirmacao_final(self) -> bool:
        return await self._evaluate("""() => {
            const senha = document.querySelector("input[type='password']");
            const botoes = [...document.querySelectorAll("input[type='submit'], button")];
            const conf = botoes.some(b => /confirmar|consolidar/i.test(b.value || b.textContent || ''));
            return !!(conf && (senha || true));
        }""")

    async def confirmar_final(self, executar: bool, contexto: str) -> None:
        """Etapa final: preenche senha (se houver) e confirma — ou para no dry-run."""
        await self.rast.etapa(self.page, "confirmacao_final", f"{contexto} (executar={executar})")

        tem_senha = False
        campo_senha = self.page.locator("input[type='password']").first
        try:
            await campo_senha.wait_for(state="visible", timeout=6000)
            await campo_senha.fill(self.cfg.senha)
            tem_senha = True
        except Exception:
            corpo = norm(await self._corpo())
            if "senha" in corpo:
                await self._screenshot_falha("senha")
                raise FluxoError("Pagina pede senha mas o campo nao foi localizado.")

        # localizar botão Confirmar
        botao = None
        for seletor in [
            "input[id='form:botaoConfirmarRegistro']",
            "input[id*='botaoConfirmarRegistro']",
            "input[type='submit'][value*='Confirmar']",
            "input[type='submit'][value*='Consolidar']",
            "input[id*='btnConfirmacao']",
            "button:has-text('Confirmar')",
        ]:
            loc = self.page.locator(seletor).first
            try:
                await loc.wait_for(state="visible", timeout=3000)
                botao = loc
                break
            except Exception:
                continue

        if botao is None:
            msgs = await self.mensagens_sigaa()
            if classificar_mensagens(msgs) == "ja_processado":
                raise JaProcessadoError(f"Ja processado: {' | '.join(msgs)}", mensagens_sigaa=msgs)
            await self._screenshot_falha("confirmar")
            raise FluxoError(
                f"Botao Confirmar nao encontrado na etapa final. URL: {self.page.url}",
                mensagens_sigaa=msgs,
            )

        if not executar:
            print(f"[DRY-RUN OK] Etapa final alcancada ({contexto}): "
                  f"senha {'preenchida' if tem_senha else 'nao requerida'}, botao Confirmar visivel. "
                  "NADA foi enviado.")
            await self.rast.screenshot(self.page, "dry_run_etapa_final")
            return

        await botao.click()
        await self.page.wait_for_timeout(3000)
        await self._esperar_pagina()
        msgs = await self.mensagens_sigaa()
        classe = classificar_mensagens(msgs)
        corpo = norm(await self._corpo())
        if classe == "ja_processado":
            raise JaProcessadoError(f"Ja processado: {' | '.join(msgs)}", mensagens_sigaa=msgs)
        if classe == "sucesso" or "sucesso" in corpo:
            print(f"[OK] {contexto} confirmado — mensagem de sucesso detectada.")
            await self.rast.screenshot(self.page, "sucesso")
            return
        await self._screenshot_falha("pos_confirmar")
        raise FluxoError(
            f"Confirmacao enviada mas o SIGAA nao exibiu mensagem de sucesso. "
            f"Mensagens: {' | '.join(msgs) if msgs else corpo[:200]}",
            mensagens_sigaa=msgs,
        )

    # ---- consolidação --------------------------------------------------------

    async def _esperar_pagina_de_conceito(self, timeout_ms: int = 10000) -> bool:
        """Igual a _pagina_de_conceito(), mas insiste enquanto o SIGAA responde."""
        limite = time.monotonic() + timeout_ms / 1000
        while True:
            if await self._pagina_de_conceito():
                return True
            if time.monotonic() >= limite:
                return False
            await self.page.wait_for_timeout(500)

    async def _pagina_de_conceito(self) -> bool:
        """True se a página atual já é a de lançamento de conceito."""
        return await self._evaluate("""() => {
            for (const sel of document.querySelectorAll('select')) {
                if (/conceito|resultado/i.test((sel.id || '') + ' ' + (sel.name || ''))) return true;
            }
            return false;
        }""")

    async def _buscar_discente_na_consolidacao(self, matricula: str) -> bool:
        """Usa o link 'Buscar Discente' da tela Consolidar Matrículas.

        A própria tela informa: "é possível buscar um discente específico para a
        consolidação acessando o link Buscar Discente". Isso resolve os casos em
        que o aluno não aparece na lista do período corrente.
        """
        link = self.page.locator("a:has-text('Buscar Discente')").first
        try:
            await link.wait_for(state="visible", timeout=4000)
        except Exception:
            return False
        print("   [INFO] Usando o link 'Buscar Discente' da tela de consolidacao...")
        url_antes = self.page.url
        try:
            async with self.page.expect_navigation(timeout=15000):
                await link.click()
        except Exception:
            if self.page.url == url_antes:
                await self.page.wait_for_timeout(2000)
        await self._esperar_pagina()
        await self.rast.screenshot(self.page, "consolidacao_buscar_discente")

        # formulário de busca (mesmo padrão da matrícula ou variante)
        preencheu = False
        for check_sel in ["[id='formulario:checkMatricula']", "input[id*='checkMatricula']"]:
            try:
                check = self.page.locator(check_sel).first
                await check.wait_for(state="visible", timeout=3000)
                if not await check.is_checked():
                    await check.check(force=True)
                break
            except Exception:
                continue
        for campo_sel in ["[id='formulario:matriculaDiscente']", "input[id*='matriculaDiscente']",
                          "input[id*='matricula' i]"]:
            try:
                campo = self.page.locator(campo_sel).first
                await campo.wait_for(state="visible", timeout=3000)
                await campo.fill(matricula)
                preencheu = True
                break
            except Exception:
                continue
        if not preencheu:
            print("   [WARN] Formulario de busca de discente nao reconhecido.")
            return False
        for botao_sel in ["[id='formulario:buscar']", "input[value='Buscar']", "input[id*='buscar']"]:
            try:
                botao = self.page.locator(botao_sel).first
                await botao.wait_for(state="visible", timeout=3000)
                await botao.click()
                break
            except Exception:
                continue
        await self._esperar_pagina()
        await self.rast.screenshot(self.page, "consolidacao_busca_resultado")

        # selecionar o aluno na lista de resultados
        marcado = await self._evaluate("""(matricula) => {
            for (const tr of document.querySelectorAll('tr')) {
                if (!tr.textContent.includes(matricula)) continue;
                const seta = tr.querySelector("input[name='form:selecionarDiscente']")
                          || tr.querySelector('input[type=image]')
                          || tr.querySelector("a[onclick*='jsfcljs']");
                if (seta) { seta.setAttribute('data-sigaa-alvo-busca', '1'); return true; }
            }
            return false;
        }""", matricula)
        if not marcado:
            msgs = await self.mensagens_sigaa()
            print(f"   [WARN] Busca nao retornou a matricula {matricula}. "
                  f"Mensagens: {' | '.join(msgs) if msgs else '(nenhuma)'}")
            return False
        url_antes = self.page.url
        try:
            async with self.page.expect_navigation(timeout=15000):
                await self.page.locator("[data-sigaa-alvo-busca='1']").first.click()
        except Exception:
            if self.page.url == url_antes:
                await self.page.wait_for_timeout(2500)
        await self._esperar_pagina()
        await self._checar_ja_processado("Busca de discente na consolidacao")
        return True

    async def selecionar_discente_consolidacao(self, matricula: str, componente_nome: str,
                                               permitir_busca: bool = True) -> None:
        """Na lista 'Consolidar Matrículas', acha a linha matrícula+componente e clica na seta.

        Estrutura real da tela (auditada): `table.listagem` com uma linha-cabeçalho
        de grupo por componente (1 única célula com `colspan`, contendo o nome do
        componente SEM o código) seguida das linhas dos discentes
        (matrícula | nome | status | período | seta `a#form:selecionar`).
        """
        await self.rast.etapa(
            self.page, "consolidacao_lista",
            f"Localizando {matricula} sob '{componente_nome}'"
        )
        await self.page.wait_for_timeout(800)

        info = await self._evaluate("""(params) => {
            const {matricula} = params;
            function normJs(s) {
                return (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\s+/g, ' ').trim();
            }
            const out = {headers: [], candidatos: []};
            let compAtual = '';
            document.querySelectorAll('tr').forEach((tr, idx) => {
                const texto = tr.textContent.trim();
                const tds = tr.querySelectorAll('td, th');
                // Linha-cabeçalho de grupo: UMA célula que ocupa a largura da tabela.
                // (o SIGAA emite <td colspan="5">ATIVIDADES COMPLEMENTARES</td>)
                if (tds.length === 1 && texto.length > 3) {
                    const unica = tds[0];
                    const colspan = parseInt(unica.getAttribute('colspan') || '1', 10);
                    const t = normJs(texto);
                    if (colspan > 1 && !/matr[ií]cula\\s*:/i.test(t) && !/^\\d+$/.test(t)) {
                        compAtual = t.toUpperCase();
                        out.headers.push(compAtual);
                        return;
                    }
                }
                if (texto.includes(matricula)) {
                    const seta = tr.querySelector("a[title*='Selecionar'], a[onclick*='jsfcljs']")
                              || tr.querySelector("input[name='form:selecionarDiscente']")
                              || tr.querySelector('input[type=image]');
                    const cels = [...tr.querySelectorAll('td')].map(td => normJs(td.textContent));
                    const periodo = cels.find(c => /^\\d{4}[.-]\\d$/.test(c)) || '';
                    out.candidatos.push({idx, componente: compAtual, periodo,
                                         texto: texto.substring(0, 150), temSeta: !!seta});
                }
            });
            return out;
        }""", {"matricula": matricula})

        self.rast.evento("consolidacao_tabela", headers=info["headers"], candidatos=info["candidatos"])

        # O componente pode vir do cabeçalho do grupo OU do próprio texto da linha.
        elegiveis = [
            c for c in info["candidatos"]
            if c["temSeta"] and (componente_casa(c["componente"], componente_nome)
                                 or componente_casa(c["texto"], componente_nome))
        ]
        # Havendo mais de uma matrícula no mesmo componente, prefere a do período pedido.
        periodo_alvo = {norm(v) for v in variacoes_periodo(self.periodo or "")} - {""}
        alvo = next((c for c in elegiveis if norm(c["periodo"]) in periodo_alvo), None) or \
               next(iter(elegiveis), None)

        if alvo is None and permitir_busca:
            # Fallback: link "Buscar Discente" da própria tela de consolidação
            if await self._buscar_discente_na_consolidacao(matricula):
                # O SIGAA responde explicitamente quando o aluno nao tem pendencia
                # (ex.: "O discente X não está matriculado em atividades acadêmicas específicas.")
                msgs = await self.mensagens_sigaa()
                corpo_msgs = norm(" | ".join(msgs))
                if "nao esta matriculado" in corpo_msgs or "nao possui matricula" in corpo_msgs:
                    await self._screenshot_falha("consolidacao_sem_pendencia")
                    raise AtividadeNaoPendenteError(
                        f"SIGAA informou que nao ha pendencia para consolidar: {' | '.join(msgs)}",
                        mensagens_sigaa=msgs,
                    )
                # A busca leva à tela "Seleção de atividade" (ou direto ao conceito).
                await self.selecionar_atividade_consolidacao(componente_nome)
                return

        if alvo is None:
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("consolidacao_sem_matricula")
            sufixo_msgs = f" Mensagens SIGAA: {' | '.join(msgs)}" if msgs else ""
            if not info["candidatos"]:
                raise FluxoError(
                    f"Matricula {matricula} nao esta na lista de consolidacao "
                    f"(componentes na pagina: {info['headers']}). "
                    "Provavel causa: aluno nao matriculado neste componente/periodo ou ja consolidado."
                    + sufixo_msgs,
                    mensagens_sigaa=msgs,
                )
            encontrados = [c["componente"] or c["texto"][:60] for c in info["candidatos"]]
            raise FluxoError(
                f"Matricula {matricula} encontrada, mas nao sob '{componente_nome}'. "
                f"Componentes onde aparece: {encontrados}. "
                "Provavel causa: componente ja consolidado ou nao matriculado."
                + sufixo_msgs,
                mensagens_sigaa=msgs,
            )

        print(f"   [OK] Linha encontrada sob '{alvo['componente']}' (periodo {alvo['periodo'] or '?'}).")
        await self._evaluate("""(idx) => {
            const tr = document.querySelectorAll('tr')[idx];
            const seta = tr.querySelector("a[title*='Selecionar'], a[onclick*='jsfcljs']")
                      || tr.querySelector("input[name='form:selecionarDiscente']")
                      || tr.querySelector('input[type=image]');
            seta.setAttribute('data-sigaa-alvo-cons', '1');
        }""", alvo["idx"])

        url_antes = self.page.url
        try:
            async with self.page.expect_navigation(timeout=15000):
                await self.page.locator("[data-sigaa-alvo-cons='1']").first.click()
        except Exception:
            if self.page.url == url_antes:
                await self.page.wait_for_timeout(2500)
        await self._esperar_pagina()
        await self._checar_ja_processado("Selecao para consolidacao")

        # Com 1 atividade o SIGAA vai direto ao conceito; com N, mostra a tela
        # "Seleção de atividade" — que precisa de mais um clique.
        await self.selecionar_atividade_consolidacao(componente_nome)

    async def selecionar_atividade_consolidacao(self, componente_nome: str) -> None:
        """Tela 'Consolidação de Atividade > Seleção de atividade'.

        Lista as atividades do discente (`SI05145 - ATIVIDADES COMPLEMENTARES - 150h`)
        com uma seta `a#form:selecionar[title='Selecionar Atividade']` por linha.
        Se a página já for a de conceito, apenas valida a atividade e retorna.
        """
        if await self._pagina_de_conceito():
            await self._validar_atividade_conceito(componente_nome)
            return

        await self.rast.etapa(
            self.page, "consolidacao_atividade",
            f"Selecionando atividade '{componente_nome}'"
        )
        linhas = await self._evaluate("""() => {
            const out = [];
            document.querySelectorAll('tr').forEach((tr, idx) => {
                const seta = tr.querySelector("a[title*='Selecionar'], a[onclick*='jsfcljs']")
                          || tr.querySelector('input[type=image]');
                if (!seta) return;
                // <script> dentro da linha suja o textContent (aparecia "... funct")
                const clone = tr.cloneNode(true);
                clone.querySelectorAll('script, style').forEach(el => el.remove());
                out.push({idx, texto: clone.textContent.replace(/\\s+/g, ' ').trim().substring(0, 200)});
            });
            return out;
        }""")
        self.rast.evento("consolidacao_atividades", linhas=linhas)

        alvo = next((l for l in linhas if componente_casa(l["texto"], componente_nome)), None)
        if alvo is None:
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("consolidacao_atividade")
            raise AtividadeNaoPendenteError(
                f"Atividade '{componente_nome}' nao esta entre as pendencias do discente. "
                f"Atividades listadas: {[l['texto'][:70] for l in linhas] or '(nenhuma)'}. "
                "Provavel causa: ja consolidada ou aluno nao matriculado nesse componente."
                + (f" Mensagens SIGAA: {' | '.join(msgs)}" if msgs else ""),
                mensagens_sigaa=msgs,
            )

        print(f"   [OK] Atividade selecionada: {alvo['texto'][:80]}")
        await self._evaluate("""(idx) => {
            const tr = document.querySelectorAll('tr')[idx];
            const seta = tr.querySelector("a[title*='Selecionar'], a[onclick*='jsfcljs']")
                      || tr.querySelector('input[type=image]');
            seta.setAttribute('data-sigaa-alvo-ativ-cons', '1');
        }""", alvo["idx"])
        url_antes = self.page.url
        try:
            async with self.page.expect_navigation(timeout=15000):
                await self.page.locator("[data-sigaa-alvo-ativ-cons='1']").first.click()
        except Exception:
            if self.page.url == url_antes:
                await self.page.wait_for_timeout(2500)
        await self._esperar_pagina()
        await self._checar_ja_processado("Selecao de atividade na consolidacao")
        await self.rast.screenshot(self.page, "consolidacao_atividade_ok")

        if not await self._esperar_pagina_de_conceito():
            await self._recuperar_pagina_vazia("consolidacao_conceito")
        if not await self._pagina_de_conceito():
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("consolidacao_sem_conceito")
            raise FluxoError(
                f"Apos selecionar a atividade nao chegou a tela de conceito. URL: {self.page.url}"
                + (f" Mensagens SIGAA: {' | '.join(msgs)}" if msgs else ""),
                mensagens_sigaa=msgs,
            )
        await self._validar_atividade_conceito(componente_nome)

    async def _validar_atividade_conceito(self, componente_nome: str) -> None:
        """Confere, na tela de conceito, que a atividade aberta é a pedida.

        A tela traz `table.formulario` com `<th>Atividade:</th><td>SI05145 - ... - 150h</td>`.
        Sem essa checagem o robô pode lançar o conceito no componente errado quando
        o discente tem mais de uma atividade pendente.
        """
        dados = await self._evaluate("""() => {
            const out = {};
            document.querySelectorAll('tr').forEach(tr => {
                const th = tr.querySelector('th');
                const td = tr.querySelector('td');
                if (!th || !td) return;
                const rot = th.textContent.replace(/\\s+/g, ' ').trim().toLowerCase();
                const val = td.textContent.replace(/\\s+/g, ' ').trim();
                if (rot.startsWith('atividade:')) out.atividade = val;
                if (rot.startsWith('tipo da atividade')) out.tipo = val;
                if (rot.startsWith('ano-per')) out.periodo = val;
            });
            return out;
        }""")
        # `dados` traz a chave 'tipo' (Tipo da Atividade); embrulha para não
        # colidir com o parâmetro `tipo` de Rastreador.evento()
        self.rast.evento("consolidacao_conceito_alvo", alvo=dados)
        atividade = dados.get("atividade") or ""
        if not atividade:
            print("   [INFO] Tela de conceito nao expoe o rotulo 'Atividade:'; seguindo.")
            return
        if not componente_casa(atividade, componente_nome):
            await self._screenshot_falha("consolidacao_atividade_divergente")
            raise FluxoError(
                f"A tela de conceito abriu a atividade '{atividade}', "
                f"mas o pedido foi '{componente_nome}'. Consolidacao abortada por seguranca."
            )
        print(f"   [OK] Atividade confirmada na tela de conceito: {atividade}"
              + (f" (periodo {dados['periodo']})" if dados.get("periodo") else ""))

    async def selecionar_conceito(self, conceito: str) -> None:
        await self.rast.etapa(self.page, "conceito", f"Selecionando conceito '{conceito}'")
        selecionado = await self._evaluate("""(conceito) => {
            const prefer = [...document.querySelectorAll('select')].filter(s =>
                /conceito|resultado/i.test((s.id || '') + ' ' + (s.name || '')));
            const todos = prefer.length ? prefer : [...document.querySelectorAll('select')];
            for (const sel of todos) {
                for (const opt of sel.options) {
                    const t = opt.text.trim().toUpperCase();
                    if (t === conceito || opt.value === conceito) {
                        sel.value = opt.value;
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        return (sel.id || sel.name || 'select') + '=' + opt.value;
                    }
                }
            }
            return null;
        }""", conceito)
        if selecionado:
            print(f"   [OK] Conceito '{conceito}' selecionado ({selecionado}).")
            self.rast.evento("conceito", resultado=selecionado)
        else:
            corpo = norm(await self._corpo())
            if "conceito" in corpo:
                await self._screenshot_falha("conceito")
                raise FluxoError(f"Nao foi possivel selecionar o conceito '{conceito}'.")
            print("   [INFO] Pagina nao tem dropdown de conceito (pode ja estar definido).")


    # ---- operações completas (reutilizáveis numa mesma sessão) ---------------

    async def preparar(self, entrada: "Entrada") -> None:
        """Login → período → portal → curso. É a parte cara e reaproveitável."""
        await self.login()
        await self.selecionar_periodo(entrada.periodo)
        await self.abrir_portal_coordenador()
        await self.selecionar_curso(entrada.curso or entrada.polo)
        self._curso_ativo = entrada.curso or entrada.polo

    async def executar_matricula(self, entrada: "Entrada") -> None:
        """Menu Matricular → discente → atividade → confirmação."""
        tipo_atividade, atividade_nome = MAPA_COMPONENTE[entrada.componente]
        if entrada.atividade_nome:
            atividade_nome = entrada.atividade_nome
        await self.menu_atividades(
            "Matricular",
            urls_esperadas=["busca_discente.jsf"],
            textos_esperados=["busca por discente", "criterios de busca"],
        )
        await self.buscar_discente(entrada.matricula)
        await self.selecionar_discente(entrada.matricula)
        await self.selecionar_atividade(tipo_atividade, atividade_nome)
        if entrada.componente.startswith("TCC") and entrada.orientador:
            await self.preencher_orientador(entrada.orientador)
        await self.proximo_passo()
        await self.confirmar_final(
            entrada.executar, f"Matricula {entrada.matricula} em {entrada.componente}"
        )

    async def executar_consolidacao(self, entrada: "Entrada") -> None:
        """Menu Consolidar Matrículas → discente/atividade → conceito → confirmação."""
        _, componente_nome = MAPA_COMPONENTE[entrada.componente]
        if entrada.atividade_nome:
            componente_nome = entrada.atividade_nome
        await self.menu_atividades(
            "Consolidar Matrículas",
            urls_esperadas=["consolida"],
            textos_esperados=["consolidar", "lista de matr"],
        )
        await self.selecionar_discente_consolidacao(entrada.matricula, componente_nome)
        await self.selecionar_conceito(entrada.conceito)
        await self.proximo_passo()
        await self.confirmar_final(
            entrada.executar,
            f"Consolidacao {entrada.matricula} em {entrada.componente} (conceito {entrada.conceito})",
        )

    async def reiniciar_para_proxima_operacao(self, entrada: "Entrada") -> None:
        """Deixa a sessão pronta para a próxima operação SEM refazer o login.

        Se a sessão tiver caído (timeout do SIGAA), refaz o login por completo.
        """
        if await self._sessao_expirada():
            print("   [INFO] Sessao do SIGAA expirou; refazendo login...")
            await self.preparar(entrada)
            return
        await self._voltar_ao_portal()
        # a troca de curso persiste na sessão; só reaplica se tiver se perdido
        curso = entrada.curso or entrada.polo
        if norm(getattr(self, "_curso_ativo", "")) != norm(curso):
            await self.selecionar_curso(curso)


# ── Fluxos completos ───────────────────────────────────────────────────────────

async def _abrir_navegador(p, entrada: Entrada, rast: Rastreador):
    browser = await p.chromium.launch(headless=entrada.headless)
    context = await browser.new_context(viewport={"width": 1400, "height": 900})
    context.set_default_timeout(20000)
    page = await context.new_page()
    rast.anexar_pagina(page)
    return browser, context, page


async def _fluxo_matricula_uma_vez(entrada: Entrada, rast: Rastreador) -> None:
    from playwright.async_api import async_playwright

    cfg = ler_config_env()

    async with async_playwright() as p:
        browser, context, page = await _abrir_navegador(p, entrada, rast)
        try:
            s = SessaoSigaa(page, cfg, rast)
            await s.preparar(entrada)
            await s.executar_matricula(entrada)

            if entrada.manter_aberto:
                print("[INFO] Navegador mantido aberto. Pressione Enter para fechar...")
                input()
        finally:
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass


async def _fluxo_consolidacao_uma_vez(entrada: Entrada, rast: Rastreador) -> None:
    from playwright.async_api import async_playwright

    cfg = ler_config_env()

    async with async_playwright() as p:
        browser, context, page = await _abrir_navegador(p, entrada, rast)
        try:
            s = SessaoSigaa(page, cfg, rast)
            await s.preparar(entrada)
            await s.executar_consolidacao(entrada)

            if entrada.manter_aberto:
                print("[INFO] Navegador mantido aberto. Pressione Enter para fechar...")
                input()
        finally:
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass


# ── Lote com sessão única ──────────────────────────────────────────────────────

@dataclass
class ItemLote:
    """Uma operação a executar dentro de uma sessão SIGAA compartilhada."""
    operacao: str          # "matricular" | "consolidar"
    entrada: Entrada
    rotulo: str = ""       # texto exibido no relatório (ex.: "Matricular ACC II")
    chave: str = ""        # identificador para dependências
    depende_de: str = ""   # chave de outro item; se ele falhar, este é PULADO

    def __post_init__(self):
        if self.operacao not in ("matricular", "consolidar"):
            raise ValueError(f"Operacao de lote invalida: {self.operacao}")
        if not self.rotulo:
            verbo = "Matricular" if self.operacao == "matricular" else "Consolidar"
            self.rotulo = f"{verbo} {self.entrada.componente}"
        if not self.chave:
            self.chave = f"{self.operacao}:{self.entrada.matricula}:{self.entrada.componente}"


@dataclass
class ResultadoLote:
    item: ItemLote
    status: str            # "ok" | "ja" | "erro"
    detalhe: str = ""


async def executar_lote(itens: list[ItemLote]) -> list[ResultadoLote]:
    """Executa várias operações reaproveitando UM navegador e UM login.

    Cada login custa uma navegação ao SIGAA — a auditoria de 02/09/2026 mostrou
    que era justamente aí que a maioria das falhas transitórias acontecia (7 de
    13 erros reais). Um aluno de 2018 exigia 8 logins (4 ACC × matrícula +
    consolidação); agora exige 1, e cada operação continua com rastreamento e
    retentativas próprias.
    """
    from playwright.async_api import async_playwright

    if not itens:
        return []
    cfg = ler_config_env()
    base = itens[0].entrada
    resultados: list[ResultadoLote] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=base.headless)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        context.set_default_timeout(20000)
        page = await context.new_page()
        sessao = SessaoSigaa(page, cfg, Rastreador("lote", ativo=False))
        preparada = False
        try:
            por_chave: dict[str, str] = {}
            for item in itens:
                print(f"\n  → {item.rotulo} ({item.entrada.matricula})")
                print(f"    {'─' * 50}")
                # Consolidar sem a matrícula ter dado certo só gera erro confuso
                # ("nao esta entre as pendencias"). Melhor pular e dizer por quê.
                if item.depende_de and por_chave.get(item.depende_de) == "erro":
                    detalhe = f"pulado: a etapa anterior ({item.depende_de}) falhou"
                    print(f"    [PULADO] {detalhe}")
                    por_chave[item.chave] = "pulado"
                    resultados.append(ResultadoLote(item, "pulado", detalhe))
                    continue
                tentativas = max(1, item.entrada.tentativas)
                status, detalhe = "erro", ""
                for i in range(1, tentativas + 1):
                    tag = (f"{'matricula' if item.operacao == 'matricular' else 'consolidacao'}"
                           f"_{item.entrada.matricula}_{norm(item.entrada.componente).replace(' ', '')}")
                    rast = Rastreador(tag, ativo=item.entrada.rastrear)
                    if rast.ativo:
                        print(f"[RASTREAMENTO] {rast.dir}")
                    rast.anexar_pagina(page)
                    sessao.rast = rast
                    if i > 1:
                        print(f"[RETRY] Tentativa {i}/{tentativas}...")
                    try:
                        if not preparada:
                            await sessao.preparar(item.entrada)
                            preparada = True
                        else:
                            await sessao.reiniciar_para_proxima_operacao(item.entrada)
                        if item.operacao == "matricular":
                            await sessao.executar_matricula(item.entrada)
                        else:
                            await sessao.executar_consolidacao(item.entrada)
                        status, detalhe = "ok", ""
                        print("    [OK] Concluído com sucesso.")
                        break
                    except JaProcessadoError as e:
                        status, detalhe = "ja", str(e)
                        print(f"    [JÁ PROCESSADO] {e}")
                        break
                    except (ValueError, ConfigError) as e:
                        status, detalhe = "erro", str(e)
                        rast.evento("falha", tentativa=i, erro=str(e)[:500])
                        print(f"    [ERRO DE ENTRADA] {e}")
                        break
                    except AtividadeNaoPendenteError as e:
                        # Determinístico: retentar não muda nada. E se a matrícula
                        # do mesmo componente disse "já matriculado", a atividade
                        # existe e não está pendente → já foi consolidada antes.
                        rast.evento("falha", tentativa=i, erro=str(e)[:500])
                        if item.depende_de and por_chave.get(item.depende_de) == "ja":
                            status = "ja"
                            detalhe = f"ja consolidada anteriormente (aluno ja matriculado): {e}"
                            print(f"    [JÁ PROCESSADO] {detalhe}")
                        else:
                            status, detalhe = "erro", str(e)
                            print(f"    [ERRO] {e}")
                        break
                    except Exception as e:
                        status, detalhe = "erro", str(e)
                        rast.evento("falha", tentativa=i, erro=str(e)[:500])
                        print(f"    [FALHA] Tentativa {i}/{tentativas}: {e}")
                    finally:
                        rast.desanexar_pagina()
                por_chave[item.chave] = status
                resultados.append(ResultadoLote(item, status, detalhe))
        finally:
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass
    return resultados


def executar_lote_sync(itens: list[ItemLote]) -> list[ResultadoLote]:
    return asyncio.run(executar_lote(itens))


async def _executar_com_retentativas(fluxo, entrada: Entrada, tag: str) -> None:
    """Roda o fluxo; em falha REAL (não JaProcessado), tenta de novo com browser novo."""
    tentativas = max(1, entrada.tentativas)
    ultima_falha: Exception | None = None
    for i in range(1, tentativas + 1):
        rast = Rastreador(f"{tag}_{entrada.matricula}_{norm(entrada.componente).replace(' ', '')}",
                          ativo=entrada.rastrear)
        if rast.ativo:
            print(f"[RASTREAMENTO] Eventos e screenshots em: {rast.dir}")
        if i > 1:
            print(f"\n[RETRY] Tentativa {i}/{tentativas}...")
        try:
            await fluxo(entrada, rast)
            return
        except JaProcessadoError:
            raise
        except (ValueError, ConfigError):
            raise
        except AtividadeNaoPendenteError as e:
            # Determinístico: a atividade não está pendente. Retentar só perde tempo.
            rast.evento("falha", tentativa=i, erro=str(e)[:500])
            raise
        except Exception as e:
            ultima_falha = e
            rast.evento("falha", tentativa=i, erro=str(e)[:500])
            print(f"[FALHA] Tentativa {i}/{tentativas}: {e}")
    raise ultima_falha  # esgotou


async def fluxo_matricula(entrada: Entrada) -> None:
    await _executar_com_retentativas(_fluxo_matricula_uma_vez, entrada, "matricula")


async def fluxo_consolidacao(entrada: Entrada) -> None:
    await _executar_com_retentativas(_fluxo_consolidacao_uma_vez, entrada, "consolidacao")


# ── CLI compartilhado ──────────────────────────────────────────────────────────

def parser_base(descricao: str, com_conceito: bool = False, com_orientador: bool = False):
    import argparse
    parser = argparse.ArgumentParser(description=descricao)
    parser.add_argument("--matricula", required=True, help="Matricula do aluno")
    parser.add_argument("--periodo", required=True, help="Periodo academico (ex: 2026.3)")
    parser.add_argument("--polo", required=True, help="Polo (usado para selecionar curso)")
    parser.add_argument("--componente", required=True, help=", ".join(sorted(MAPA_COMPONENTE)))
    if com_conceito:
        parser.add_argument("--conceito", default="E", help="Conceito (padrao: E)")
    if com_orientador:
        parser.add_argument("--orientador", required=True, help="Nome do orientador (como no SIGAA)")
    parser.add_argument("--curso", default=None, help="Texto do curso (sobrescreve --polo)")
    parser.add_argument("--atividade-nome", default=None, help="Nome exato da atividade (sobrescreve o mapa)")
    parser.add_argument("--executar", action="store_true", help="Confirma a operacao (sem isso: dry-run)")
    parser.add_argument("--headless", action="store_true", help="Sem janela do navegador")
    parser.add_argument("--manter-aberto", action="store_true", help="Mantem navegador aberto ao final")
    parser.add_argument("--sem-rastreio", dest="rastrear", action="store_false",
                        help="Desliga o rastreamento (JSONL + screenshots)")
    parser.add_argument("--tentativas", type=int, default=2, help="Tentativas em caso de falha (padrao: 2)")
    return parser


def executar_cli(coro) -> None:
    """Roda o fluxo e converte exceções em exit codes: 0 ok, 3 já processado, 2 erro."""
    import sys
    try:
        asyncio.run(coro)
    except JaProcessadoError as e:
        print(f"\n[JA PROCESSADO] {e}")
        sys.exit(3)
    except (ValueError, ConfigError) as e:
        print(f"\n[ERRO DE ENTRADA] {e}")
        sys.exit(2)
    except Exception as e:
        print(f"\n[ERRO] {e}")
        sys.exit(2)
