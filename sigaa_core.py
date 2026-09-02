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
            msgs = await self.page.evaluate("""() => {
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

    # ---- etapas comuns ------------------------------------------------------

    async def login(self) -> None:
        await self.rast.etapa(None, "login", "Abrindo SIGAA e autenticando")
        await self.page.goto(self.cfg.sigaa_url, wait_until="domcontentloaded")

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
            await self.page.goto(f"{base}/sigaa/verPortalCoordenadorGraduacao.do", wait_until="domcontentloaded")
            await self._esperar_pagina()
        if "coordenador.jsf" not in self.page.url:
            await self._screenshot_falha("portal")
            raise FluxoError(f"Nao abriu Portal Coord. Graduacao. URL: {self.page.url}")

    async def selecionar_curso(self, polo_ou_curso: str) -> None:
        """Seleciona o curso no dropdown do portal e VERIFICA que ele foi aplicado."""
        await self.rast.etapa(self.page, "curso", f"Selecionando curso por '{polo_ou_curso}'")
        alvo = norm(polo_ou_curso)

        for tentativa in range(2):
            selecionou = await self.page.evaluate("""(alvo) => {
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
                await self._screenshot_falha("curso")
                raise FluxoError(
                    f"Curso/polo '{polo_ou_curso}' nao encontrado no dropdown do portal. "
                    "Confira o nome do polo (ex.: CAMETA, LIMOEIRO DO AJURU, OEIRAS DO PARA)."
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

    # ---- menu Atividades (determinístico via jscook_action) ------------------

    async def _extrair_acoes_menu(self) -> dict:
        """Lê os <script> do portal e devolve {label_normalizada: (form, jscook_action)}.

        Formato real dos itens do JSCookMenu no SIGAA (confirmado no portal):
            ['<img .../>', 'Consolidar Matr&#237;culas',
             'menu_coordenador_..._menu:A]#{ registroAtividade.iniciarConsolidarMatriculas}',
             'menu_coordenador', null]
        A string de ação JÁ inclui o prefixo do menu; o 4º campo é o id do form.
        """
        dados = await self.page.evaluate(r"""() => {
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
            resultado = await self.page.evaluate("""(params) => {
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
            ativ_box = await self.page.evaluate("""() => {
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
            alvo = await self.page.evaluate("""(partial) => {
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

        async def _chegou() -> bool:
            if any(u in self.page.url for u in urls_esperadas):
                return True
            corpo = norm(await self._corpo())
            return any(norm(t) in corpo for t in textos_esperados)

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
            await self._submeter_jscook_action(form_id, action)
            if await _chegou():
                print(f"   [OK] Navegou via jscook_action. URL={self.page.url}")
                await self.rast.screenshot(self.page, f"menu_{rotulo}_ok")
                return
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
        marcado = await self.page.evaluate("""(matricula) => {
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

        chegou = await self.page.evaluate("""() => {
            return !!(document.querySelector("[id='form:idTipoAtividade']")
                   || document.querySelector("[id='form:atividades']"));
        }""")
        if not chegou and "busca_atividade" not in self.page.url:
            msgs = await self.mensagens_sigaa()
            await self._screenshot_falha("apos_selecionar_discente")
            raise FluxoError(
                f"Apos selecionar o discente, a pagina de busca de atividade nao abriu. URL: {self.page.url}",
                mensagens_sigaa=msgs,
            )

    async def selecionar_atividade(self, tipo_atividade: str, atividade_nome: str) -> None:
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
            linhas = await self.page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('table tr').forEach((tr, i) => {
                    const temSeta = !!tr.querySelector('input[type=image]');
                    if (temSeta) out.push({idx: i, texto: tr.textContent.trim().substring(0, 200)});
                });
                return out;
            }""")
            alvo_linha = next(
                (l for l in linhas if contem_componente_exato(l["texto"], atividade_nome)), None
            )
            if alvo_linha is not None:
                await self.page.evaluate("""(idx) => {
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

            id_orientador = await self.page.evaluate("""() => {
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
        id_orientador = await self.page.evaluate("""() => {
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
                await self.page.evaluate(
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
        return await self.page.evaluate("""() => {
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

    async def _pagina_de_conceito(self) -> bool:
        """True se a página atual já é a de lançamento de conceito."""
        return await self.page.evaluate("""() => {
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
        marcado = await self.page.evaluate("""(matricula) => {
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
        """Na lista 'Consolidar Matrículas', acha a linha matrícula+componente e clica na seta."""
        await self.rast.etapa(
            self.page, "consolidacao_lista",
            f"Localizando {matricula} sob '{componente_nome}'"
        )
        await self.page.wait_for_timeout(800)

        info = await self.page.evaluate("""(params) => {
            const {matricula} = params;
            function normJs(s) {
                return (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
                    .replace(/\\s+/g, ' ').trim();
            }
            const out = {headers: [], candidatos: []};
            let compAtual = '';
            document.querySelectorAll('tr').forEach((tr, idx) => {
                const texto = tr.textContent.trim();
                const tds = tr.querySelectorAll('td');
                // linha-cabeçalho de componente: poucas células, texto maiúsculo longo
                if (tds.length <= 2 && texto.length > 5) {
                    const t = normJs(texto).toUpperCase();
                    if (/^[A-Z0-9\\s]+[IVX]*$/.test(t) && !t.includes('MATR')) {
                        compAtual = t;
                        out.headers.push(t);
                        return;
                    }
                }
                if (texto.includes(matricula)) {
                    const seta = tr.querySelector("input[name='form:selecionarDiscente']")
                              || tr.querySelector('input[type=image]')
                              || tr.querySelector("a[onclick*='jsfcljs']");
                    out.candidatos.push({idx, componente: compAtual,
                                         texto: texto.substring(0, 150), temSeta: !!seta});
                }
            });
            return out;
        }""", {"matricula": matricula})

        self.rast.evento("consolidacao_tabela", headers=info["headers"], candidatos=info["candidatos"])

        alvo = next(
            (c for c in info["candidatos"]
             if c["temSeta"] and (contem_componente_exato(c["componente"], componente_nome)
                                  or contem_componente_exato(c["texto"], componente_nome))),
            None,
        )

        if alvo is None and permitir_busca:
            # Fallback: link "Buscar Discente" da própria tela de consolidação
            if await self._buscar_discente_na_consolidacao(matricula):
                # O SIGAA responde explicitamente quando o aluno nao tem pendencia
                # (ex.: "O discente X não está matriculado em atividades acadêmicas específicas.")
                msgs = await self.mensagens_sigaa()
                corpo_msgs = norm(" | ".join(msgs))
                if "nao esta matriculado" in corpo_msgs or "nao possui matricula" in corpo_msgs:
                    await self._screenshot_falha("consolidacao_sem_pendencia")
                    raise FluxoError(
                        f"SIGAA informou que nao ha pendencia para consolidar: {' | '.join(msgs)}",
                        mensagens_sigaa=msgs,
                    )
                if await self._pagina_de_conceito():
                    print("   [OK] Busca de discente levou direto a pagina de conceito.")
                    return
                # a página agora lista as matrículas do próprio aluno
                return await self.selecionar_discente_consolidacao(
                    matricula, componente_nome, permitir_busca=False
                )

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
                "Provavel causa: componente ja consolidado ou nao matriculado.",
                mensagens_sigaa=msgs,
            )

        print(f"   [OK] Linha encontrada sob '{alvo['componente']}'.")
        await self.page.evaluate("""(idx) => {
            const tr = document.querySelectorAll('tr')[idx];
            const seta = tr.querySelector("input[name='form:selecionarDiscente']")
                      || tr.querySelector('input[type=image]')
                      || tr.querySelector("a[onclick*='jsfcljs']");
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

    async def selecionar_conceito(self, conceito: str) -> None:
        await self.rast.etapa(self.page, "conceito", f"Selecionando conceito '{conceito}'")
        selecionado = await self.page.evaluate("""(conceito) => {
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
    tipo_atividade, atividade_nome = MAPA_COMPONENTE[entrada.componente]
    if entrada.atividade_nome:
        atividade_nome = entrada.atividade_nome

    async with async_playwright() as p:
        browser, context, page = await _abrir_navegador(p, entrada, rast)
        try:
            s = SessaoSigaa(page, cfg, rast)
            await s.login()
            await s.selecionar_periodo(entrada.periodo)
            await s.abrir_portal_coordenador()
            await s.selecionar_curso(entrada.curso or entrada.polo)
            await s.menu_atividades(
                "Matricular",
                urls_esperadas=["busca_discente.jsf"],
                textos_esperados=["busca por discente", "criterios de busca"],
            )
            await s.buscar_discente(entrada.matricula)
            await s.selecionar_discente(entrada.matricula)
            await s.selecionar_atividade(tipo_atividade, atividade_nome)
            if entrada.componente.startswith("TCC") and entrada.orientador:
                await s.preencher_orientador(entrada.orientador)
            await s.proximo_passo()
            await s.confirmar_final(entrada.executar, f"Matricula {entrada.matricula} em {entrada.componente}")

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
    _, componente_nome = MAPA_COMPONENTE[entrada.componente]
    if entrada.atividade_nome:
        componente_nome = entrada.atividade_nome

    async with async_playwright() as p:
        browser, context, page = await _abrir_navegador(p, entrada, rast)
        try:
            s = SessaoSigaa(page, cfg, rast)
            await s.login()
            await s.selecionar_periodo(entrada.periodo)
            await s.abrir_portal_coordenador()
            await s.selecionar_curso(entrada.curso or entrada.polo)
            await s.menu_atividades(
                "Consolidar Matrículas",
                urls_esperadas=["consolida"],
                textos_esperados=["consolidar", "lista de matr"],
            )
            await s.selecionar_discente_consolidacao(entrada.matricula, componente_nome)
            await s.selecionar_conceito(entrada.conceito)
            await s.proximo_passo()
            await s.confirmar_final(
                entrada.executar,
                f"Consolidacao {entrada.matricula} em {entrada.componente} (conceito {entrada.conceito})",
            )

            if entrada.manter_aberto:
                print("[INFO] Navegador mantido aberto. Pressione Enter para fechar...")
                input()
        finally:
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass


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
