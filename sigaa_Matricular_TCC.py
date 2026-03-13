"""
sigaa_Matricular_TCC.py — Matricula em Atividade (TCC I) no SIGAA.

Fluxo:
  [1/9] Abrir navegador e SIGAA
  [2/9] Login
  [3/9] Selecionar período acadêmico
  [4/9] Abrir Portal Coord. Graduação + selecionar curso/polo
  [5/9] Menu Atividades > Matricular
  [6/9] Buscar aluno por matrícula
  [7/9] Selecionar discente na lista
  [8/9] Selecionar tipo de atividade "TRABALHO DE CONCLUSAO DE CURSO" e buscar/selecionar componente "TRABALHO DE CONCLUSAO DE CURSO I" (ou conforme config)
  [9/9] Preencher Orientador (obrigatório para TCC), preencher senha e confirmar.

Uso:
  python sigaa_Matricular_TCCI.py \
      --matricula 202416040009 \
      --periodo 2026.1 \
      --polo "CAMETA" \
      --componente "TCC I" \
      --orientador "ELTON SARMANHO SIQUEIRA" \
      --executar

"""

import argparse
import asyncio
import json
import os
import re
import unicodedata
from urllib.parse import urlparse
from dataclasses import dataclass

from dotenv import load_dotenv

# ── Constantes ─────────────────────────────────────────────────────────────────

COMPONENTES_VALIDOS = {"TCC", "TCC I", "TCC II"}
MAPA_COMPONENTE = {
    "TCC": ("TRABALHO DE CONCLUSÃO DE CURSO", "TRABALHO DE CONCLUSAO DE CURSO"),
    "TCC I": ("TRABALHO DE CONCLUSÃO DE CURSO", "TRABALHO DE CONCLUSAO DE CURSO I"),
    "TCC II": ("TRABALHO DE CONCLUSÃO DE CURSO", "TRABALHO DE CONCLUSAO DE CURSO II"),
}

# ── Data classes ───────────────────────────────────────────────────────────────

class ConfigError(RuntimeError):
    pass


@dataclass
class ConfigSigaa:
    login: str
    senha: str
    sigaa_url: str


@dataclass
class EntradaLancamento:
    matricula: str
    periodo: str
    polo: str
    componente: str
    orientador: str

# ── Helpers reutilizados ───────────────────────────────────────────────────────

def norm(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    ascii_only = base.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def ler_config_env() -> ConfigSigaa:
    load_dotenv()
    login = os.getenv("LOGIN")
    senha = os.getenv("SENHA")
    sigaa_url = os.getenv("SIGAA_URL")

    faltando = [
        nome
        for nome, valor in {"LOGIN": login, "SENHA": senha, "SIGAA_URL": sigaa_url}.items()
        if not valor
    ]
    if faltando:
        raise ConfigError(f"Variaveis obrigatorias faltando no .env: {', '.join(faltando)}")

    return ConfigSigaa(login=login, senha=senha, sigaa_url=sigaa_url)


def validar_entrada(entrada: EntradaLancamento) -> None:
    if entrada.componente.upper() not in COMPONENTES_VALIDOS:
        validos = ", ".join(sorted(COMPONENTES_VALIDOS))
        raise ValueError(f"Componente invalido: {entrada.componente}. Use: {validos}")


async def clicar_primeiro_visivel(page, seletores: list[str], timeout_ms: int = 7000) -> bool:
    for seletor in seletores:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click()
            return True
        except Exception:
            continue
    return False


async def preencher_primeiro_visivel(page, seletores: list[str], valor: str, timeout_ms: int = 7000) -> bool:
    for seletor in seletores:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.fill(valor)
            return True
        except Exception:
            continue
    return False


async def preencher_input_por_rotulo(page, rotulo_regex: str, valor: str) -> bool:
    candidatos = [
        f"xpath=//tr[.//*[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]]//input[not(@type='checkbox') and not(@type='hidden')][1]",
        f"xpath=//td[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]/following::input[not(@type='checkbox') and not(@type='hidden')][1]",
    ]
    for seletor in candidatos:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=2000)
            await loc.fill(valor)
            # Para autocompletes SIGAA, eh bom disparar evento de input
            await loc.dispatchEvent('keyup', {'key': 'Enter'})
            await page.wait_for_timeout(1000)
            return True
        except Exception:
            continue
    return False

def variacoes_periodo(periodo: str) -> list[str]:
    base = periodo.strip()
    return [base, base.replace(".", "-"), base.replace("-", ".")]


def base_sigaa_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


async def selecionar_opcao_em_dropdown(page, opcao_desejada: str, filtro_dropdown: str | None = None) -> bool:
    select_loc = page.locator("select")
    total = await select_loc.count()
    alvo = norm(opcao_desejada)
    filtro = norm(filtro_dropdown) if filtro_dropdown else None

    for i in range(total):
        sel = select_loc.nth(i)

        if filtro:
            descritor = await sel.evaluate(
                "el => [el.id || '', el.name || '', el.getAttribute('aria-label') || '', el.className || ''].join(' ')"
            )
            if filtro not in norm(descritor):
                continue

        opcoes = await sel.locator("option").all_text_contents()
        candidata = next((o for o in opcoes if alvo in norm(o)), None)
        if not candidata:
            continue

        try:
            await sel.select_option(label=candidata.strip())
            return True
        except Exception:
            try:
                valor = await sel.locator("option", has_text=candidata.strip()).first.get_attribute("value")
                if valor is not None:
                    await sel.select_option(value=valor)
                    return True
            except Exception:
                continue

    return False


async def clicar_seta_selecao_discente(page, matricula: str) -> bool:
    linha = page.locator(f"tr:has-text('{matricula}')").first
    try:
        if await linha.count():
            seta = linha.locator("input[type='image']").first
            if await seta.count():
                url_antes = page.url
                try:
                    async with page.expect_navigation(timeout=10000):
                        await seta.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err) or page.url != url_antes:
                        return True
                    await page.wait_for_timeout(3000)
                    if page.url != url_antes:
                        return True
                else:
                    return True

            seta_link = linha.locator("a:has(img[src*='seta'])").first
            if await seta_link.count():
                try:
                    await seta_link.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True

            jsf_link = linha.locator("a[onclick*='jsfcljs']").first
            if await jsf_link.count():
                try:
                    await jsf_link.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True

            all_links = linha.locator("a")
            count = await all_links.count()
            if count > 0:
                alvo = all_links.nth(count - 1)
                try:
                    await alvo.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True
    except Exception as e:
        if _is_navigation_error(e):
            return True

    linha_formando = page.locator("tr:has-text('FORMANDO')").first
    try:
        if await linha_formando.count():
            seta = linha_formando.locator("input[type='image']").first
            if await seta.count():
                try:
                    await seta.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True
    except Exception as e:
        if _is_navigation_error(e):
            return True

    return await clicar_primeiro_visivel(
        page,
        [
            "a[onclick*='jsfcljs']",
            "a[title*='Selecion']",
            "img[alt*='Selecion']",
            "input[type='image'][alt*='Selecion']",
            "a:has(i.fa-arrow-right)",
        ],
        timeout_ms=4000,
    )


def _is_navigation_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in [
        "execution context was destroyed",
        "most likely because of a navigation",
        "navigating",
        "target page, context or browser has been closed",
        "frame was detached",
    ])


async def _check_navegou(page, fragmento_url: str, timeout_ms: int = 5000) -> bool:
    if fragmento_url in page.url:
        return True
    try:
        await page.wait_for_url(f"**{fragmento_url}**", timeout=timeout_ms)
        return True
    except Exception:
        return fragmento_url in page.url


async def _aguardar_navegacao(page, esperado_na_url: str, timeout_ms: int = 10000) -> bool:
    try:
        await page.wait_for_url(f"**{esperado_na_url}**", timeout=timeout_ms)
        return True
    except Exception:
        return esperado_na_url in page.url


async def _clicar_menu_atividades_matricular(page) -> bool:
    # ---------- garantir pagina pronta ----------
    try:
        await page.wait_for_load_state("load", timeout=8000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # ---------- encontrar posicao do td Atividades ----------
    ativ_box = None
    try:
        ativ_box = await page.evaluate("""() => {
            for (const td of document.querySelectorAll('td')) {
                if ((td.className.includes('ThemeOfficeMainItem')
                     || td.className.includes('ThemeOfficeMainItemHover'))
                    && /Atividades/i.test(td.textContent.trim())) {
                    const r = td.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        return {x: r.x + r.width/2, y: r.y + r.height/2,
                                bottom: r.bottom, left: r.left, width: r.width};
                    }
                }
            }
            return null;
        }""")
    except Exception as e:
        print(f"   [WARN] Nao encontrou td Atividades: {e}")

    if not ativ_box:
        return False

    for tentativa in range(3):
        await page.mouse.move(ativ_box['x'], ativ_box['y'])
        await page.wait_for_timeout(800 + tentativa * 300)

        matricular_info = await page.evaluate("""() => {
            const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
            const candidates = [];
            for (const tr of trs) {
                const text = tr.textContent.trim();
                if (text === 'Matricular') {
                    const rect = tr.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        const div = tr.closest('div');
                        const style = div ? getComputedStyle(div) : null;
                        const divVisible = !style
                            || (style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && parseFloat(style.opacity) > 0);
                        candidates.push({
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            divVisible: divVisible,
                        });
                    }
                }
            }
            const visible = candidates.filter(c => c.divVisible);
            return visible.length > 0 ? visible[0]
                 : candidates.length > 0 ? candidates[0]
                 : null;
        }""")

        if not matricular_info:
            await page.mouse.move(ativ_box['x'], ativ_box['bottom'] + 10)
            await page.wait_for_timeout(500)
            matricular_info = await page.evaluate("""() => {
                const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
                for (const tr of trs) {
                    if (tr.textContent.trim() === 'Matricular') {
                        const rect = tr.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.top > 0) {
                            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2, divVisible: true};
                        }
                    }
                }
                return null;
            }""")

        if not matricular_info:
            await page.mouse.move(0, 0)
            await page.wait_for_timeout(500)
            continue

        target_x = matricular_info['x']
        target_y = matricular_info['y']
        mid_y = ativ_box.get('bottom', ativ_box['y'] + 15) + 5
        
        await page.mouse.move(ativ_box['x'], mid_y, steps=3)
        await page.wait_for_timeout(150)
        await page.mouse.move(target_x, mid_y, steps=3)
        await page.wait_for_timeout(150)
        await page.mouse.move(target_x, target_y, steps=5)
        await page.wait_for_timeout(200)

        try:
            async with page.expect_navigation(timeout=8000):
                await page.mouse.click(target_x, target_y)
            if "busca_discente" in page.url:
                return True
        except Exception as click_err:
            if _is_navigation_error(click_err) or "busca_discente" in page.url:
                if await _check_navegou(page, "busca_discente", 5000):
                    return True

    # FALLBACK
    await page.mouse.move(ativ_box['x'], ativ_box['y'])
    await page.wait_for_timeout(800)

    submenu_items = await page.evaluate("""() => {
        const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
        const items = [];
        for (const tr of trs) {
            const text = tr.textContent.trim();
            const rect = tr.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && rect.top > 0) {
                items.push({text, x: rect.x + rect.width/2, y: rect.y + rect.height/2});
            }
        }
        return items;
    }""")

    if submenu_items:
        target = None
        for item in submenu_items:
            txt = item['text'].strip()
            if txt.lower() == 'matricular':
                target = item
                break
        if not target:
            for item in submenu_items:
                txt = item['text'].strip().lower()
                if 'matricular' in txt and 'desmatricul' not in txt and 'matriculado' not in txt:
                    target = item
                    break

        if target:
            await page.mouse.move(ativ_box['x'], ativ_box['y'])
            await page.wait_for_timeout(600)
            await page.mouse.move(target['x'], target['y'], steps=10)
            await page.wait_for_timeout(200)
            try:
                async with page.expect_navigation(timeout=8000):
                    await page.mouse.click(target['x'], target['y'])
                if "busca_discente" in page.url:
                    return True
            except Exception as e:
                if _is_navigation_error(e):
                    if await _check_navegou(page, "busca_discente", 5000):
                        return True

    return False


async def executar_fluxo_direto(args: argparse.Namespace) -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as err:
        raise RuntimeError(
            "Playwright nao encontrado. Instale com: pip install playwright && playwright install chromium"
        ) from err

    cfg = ler_config_env()
    entrada = EntradaLancamento(
        matricula=args.matricula,
        periodo=args.periodo,
        polo=args.polo,
        componente=args.componente.upper(),
        orientador=args.orientador,
    )
    validar_entrada(entrada)

    tipo_atividade, atividade_nome = MAPA_COMPONENTE[entrada.componente]
    if args.atividade_nome:
        atividade_nome = args.atividade_nome

    base = base_sigaa_url(cfg.sigaa_url)

    print("[1/9] Abrindo navegador e SIGAA...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)
        context = await browser.new_context()
        page = await context.new_page()

        # --- LOGIN ---
        print("[2/9] Login...")
        await page.goto(cfg.sigaa_url, wait_until="domcontentloaded")

        login_field = page.locator("input[name='user.login']").first
        await login_field.wait_for(state="visible", timeout=10000)
        await login_field.fill(cfg.login)

        senha_field = page.locator("input[name='user.senha']").first
        await senha_field.fill(cfg.senha)

        submit = page.locator("input[type='submit']").first
        await submit.click()
        await page.wait_for_load_state("domcontentloaded")

        # --- SELECAO DE PERIODO NO CALENDARIO ---
        print("[3/9] Selecionando periodo academico no calendario...")
        await page.wait_for_load_state("domcontentloaded")
        if "calendarios" in page.url or "verTelaLogin" not in page.url:
            clicou_periodo = False
            for variacao in variacoes_periodo(entrada.periodo):
                try:
                    link_periodo = page.locator(
                        f"a:has-text('{variacao}')"
                    ).first
                    await link_periodo.wait_for(state="visible", timeout=3000)
                    await link_periodo.click()
                    await page.wait_for_load_state("domcontentloaded")
                    clicou_periodo = True
                    break
                except Exception:
                    continue

            if not clicou_periodo:
                try:
                    link_qualquer = page.locator(
                        "table a[href*='verMenuPrincipal'], "
                        "table a[href*='calendario'], "
                        "td.destaque a, "
                        "td a[href*='periodo']"
                    ).first
                    await link_qualquer.wait_for(state="visible", timeout=3000)
                    await link_qualquer.click()
                    await page.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass

        # --- PORTAL COORD. GRADUACAO ---
        print("[4/9] Abrindo Portal Coord. Graduacao...")
        if "coordenador.jsf" not in page.url:
            link_portal = page.locator("a[href*='verPortalCoordenadorGraduacao']").first
            try:
                await link_portal.wait_for(state="visible", timeout=5000)
                await link_portal.click()
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                portal_do_url = f"{base}/sigaa/verPortalCoordenadorGraduacao.do"
                await page.goto(portal_do_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("domcontentloaded")

        if "coordenador.jsf" not in page.url:
            raise RuntimeError(f"Nao foi possivel abrir o Portal Coord. Graduacao. URL atual: {page.url}")

        # --- SELECAO DE CURSO ---
        print("[5/9] Selecionando curso pelo polo...")
        await page.wait_for_load_state("domcontentloaded")
        alvo_curso = args.curso if args.curso else entrada.polo
        selecionou = await selecionar_opcao_em_dropdown(page, alvo_curso)
        if selecionou:
            try:
                await page.wait_for_load_state("load", timeout=10000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)

        # --- MENU ATIVIDADES > MATRICULAR ---
        print("[6/9] Menu Atividades > Matricular...")
        if not await _clicar_menu_atividades_matricular(page):
            try:
                await page.screenshot(path="/tmp/sigaa_menu_fail.png")
            except Exception:
                pass
            raise RuntimeError("Nao foi possivel navegar pelo menu Atividades > Matricular.")

        navegou = await _aguardar_navegacao(page, "busca_discente.jsf", timeout_ms=10000)
        if not navegou:
            await page.wait_for_timeout(2000)
            navegou = "busca_discente.jsf" in page.url

        # --- BUSCA DO DISCENTE ---
        print("[7/9] Buscando aluno por matricula...")
        await page.wait_for_load_state("domcontentloaded")

        check_mat = page.locator('[id="formulario:checkMatricula"]').first
        try:
            await check_mat.wait_for(state="visible", timeout=5000)
            await check_mat.click()
        except Exception:
            await clicar_primeiro_visivel(
                page,
                ["input[type='checkbox'][id*='checkMatricula']", "input[type='radio'][id*='checkMatricula']"],
                timeout_ms=3000,
            )

        campo_mat = page.locator('[id="formulario:matriculaDiscente"]').first
        try:
            await campo_mat.wait_for(state="visible", timeout=5000)
            await campo_mat.fill(entrada.matricula)
        except Exception:
            if not await preencher_primeiro_visivel(
                page,
                ["input[id*='matriculaDiscente']", "input[title*='Matrícula']", "input[title*='Matricula']"],
                entrada.matricula,
            ):
                raise RuntimeError("Nao foi possivel preencher o campo de matricula.")

        buscar_btn = page.locator('[id="formulario:buscar"]').first
        try:
            await buscar_btn.wait_for(state="visible", timeout=5000)
            await buscar_btn.click()
        except Exception:
            if not await clicar_primeiro_visivel(
                page,
                ["input[value='Buscar']", "input[id*='buscar']", "button:has-text('Buscar')"],
            ):
                raise RuntimeError("Nao foi possivel clicar em Buscar.")

        await page.wait_for_load_state("domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        # --- SELECAO DO DISCENTE ---
        print("[8/9] Selecionando discente na lista...")
        url_antes = page.url
        if not await clicar_seta_selecao_discente(page, entrada.matricula):
            await page.wait_for_timeout(2000)
            if not await clicar_seta_selecao_discente(page, entrada.matricula):
                raise RuntimeError("Nao foi possivel selecionar o discente na lista de resultados.")

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        navegou = await _aguardar_navegacao(page, "busca_atividade.jsf", timeout_ms=10000)
        if not navegou:
            await page.wait_for_timeout(3000)
            navegou = "busca_atividade.jsf" in page.url

        # JSF forward fallback
        if not navegou:
            try:
                page_check = await page.evaluate("""() => {
                    return {
                        hasAtividadeDropdown: !!document.querySelector('[id*="idTipoAtividade"]'),
                        hasBuscarAtividades: !!document.querySelector('[id*="atividades"]')
                    };
                }""")
                if page_check.get('hasAtividadeDropdown') or page_check.get('hasBuscarAtividades'):
                    navegou = True
            except Exception:
                pass


        # --- SELECAO DE ATIVIDADE ---
        print(f"[9/9] Selecionando tipo de atividade e buscando componente '{atividade_nome}'...")
        await page.wait_for_load_state("domcontentloaded")

        # Selecionar "TRABALHO DE CONCLUSAO DE CURSO" no Dropdown
        sel_tipo = page.locator('[id="form:idTipoAtividade"]').first
        try:
            await sel_tipo.wait_for(state="visible", timeout=5000)
            await sel_tipo.select_option(label=tipo_atividade)
        except Exception:
            if not await selecionar_opcao_em_dropdown(page, tipo_atividade, filtro_dropdown="tipoAtividade"):
                raise RuntimeError(f"Nao foi possivel selecionar tipo de atividade: {tipo_atividade}")

        # Buscar Atividades
        buscar_ativ = page.locator('[id="form:atividades"]').first
        try:
            await buscar_ativ.wait_for(state="visible", timeout=5000)
            await buscar_ativ.click()
        except Exception:
            if not await clicar_primeiro_visivel(
                page,
                ["input[value*='Buscar Atividades']", "input[id*='atividades']"],
            ):
                raise RuntimeError("Nao foi possivel clicar em Buscar Atividades.")

        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1000)

        # Clicar na Seta verde
        encontrou_atividade = False
        
        # Tentar achar na tabela atual
        linhas_tabela = await page.locator("table.listagem tbody tr").all()
        for linha in linhas_tabela:
            texto_coluna1 = await linha.locator("td").first.inner_text()
            # Procurar pelo termo exato, garantindo nao confundir "TCC" com "TCC I"
            if atividade_nome.upper() in texto_coluna1.upper():
                # Regras rígidas para diferenciar as versões I e II da versão base
                if atividade_nome == "TRABALHO DE CONCLUSAO DE CURSO":
                    if " I" in texto_coluna1 or " II" in texto_coluna1:
                        continue
                elif atividade_nome == "TRABALHO DE CONCLUSAO DE CURSO I":
                    if " II" in texto_coluna1 or not texto_coluna1.endswith(" I"):
                        if "TRABALHO DE CONCLUSAO DE CURSO I" not in texto_coluna1:
                            continue
                
                # Achou a linha certa, buscar o input/image
                alvo_el = linha.locator("input[type='image'][src*='seta']").first
                if not await alvo_el.count():
                    alvo_el = linha.locator("a, input[type='submit']").first
                    
                if await alvo_el.count():
                    try:
                        await alvo_el.scroll_into_view_if_needed()
                        await page.wait_for_timeout(500)
                        await alvo_el.click(timeout=5000)
                        encontrou_atividade = True
                        break
                    except Exception as e:
                        print(f"Erro ao clicar na seta da atividade: {e}")
                        continue
        
        # Se não encontrou, usar o fallback original de buscar pelo codigo
        if not encontrou_atividade:
            campo_codigo = page.locator("input[id*='codigoComponente'], input[id*='codigo'], input[id*='nomeComponente'], input[id*='nomeAtividadeInput']").first
            try:
                if await campo_codigo.count():
                    await campo_codigo.fill(atividade_nome)
                    buscar_ativ2 = page.locator('[id="form:atividades"]').first
                    await buscar_ativ2.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(1000)
                    
                    # Tentar de novo pos submit (com a logica simplificada, pois ja enviou o form)
                    linha = page.locator(f"tr:has-text('{atividade_nome}')").first
                    if await linha.count():
                        seta = linha.locator("input[type='image'][src*='seta']").first
                        if await seta.count():
                            await seta.click(timeout=5000)
                            encontrou_atividade = True
            except Exception:
                pass

        if not encontrou_atividade:
            raise RuntimeError(f"Atividade '{atividade_nome}' nao encontrada na tabela de resultados.")

        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(500)

        # Clicar Confirmar/Próximo Passo
        if "dados_registro.jsf" not in page.url:
            clicou_proximo = await clicar_primeiro_visivel(
                page,
                [
                    "input[type='submit'][value*='Próximo']:not([id*='btnAtividades'])",
                    "input[type='submit'][value*='Proximo']:not([id*='btnAtividades'])",
                    "button:has-text('Próximo')",
                ],
                timeout_ms=5000,
            )

        # --- TELA DADOS DE REGISTRO / ORIENTADOR E SENHA ---
        print("[-] Preenchendo Orientador, Senha e Confirmando...")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1500)
        
        # Preencher Orientador (necessario para TCC)
        if entrada.orientador:
            try:
                # O input no sigaa geralmente é autocomplete
                # Usar xpath rigoroso: encontra o <th> que o texto exato começa com "Orientador:" e pega o input text
                input_xpath = "xpath=//th[normalize-space(text())='Orientador:']/following-sibling::td//input[@type='text']"
                
                orientador_input = page.locator(input_xpath).first
                if not await orientador_input.count():
                    # Fallback robusto usando regex de ID exato de JSF (terminando em orientador, nao coorientador)
                    orientador_input = page.locator("input[id$='ri|entador']:not([id*='coorientador']), input[name$='rientador']:not([name*='coorientador'])").first
                    if not await orientador_input.count():
                        # Ultimo recurso: o PRIMEIRO input de texto da div principal que nao esta escondido (Orientador vem antes de Coorientador)
                        orientador_input = page.locator("table.formulario input[type='text']:not([id*='coorientador'])").nth(1)

                await orientador_input.wait_for(state="visible", timeout=5000)
                await orientador_input.fill(entrada.orientador)
                
                # Sigaa Autocomplete
                await page.wait_for_timeout(800)
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(500)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(500)
            except Exception:
                # Tenta fallback usando helpers
                await preencher_input_por_rotulo(page, "Orientador:", entrada.orientador)
                
            # Prosseguir Passo (apos orientador)
            await clicar_primeiro_visivel(
                page,
                [
                    "input[type='submit'][value*='Próximo Passo']",
                    "input[type='submit'][value*='Proximo Passo']",
                    "button:has-text('Próximo Passo')"
                ],
                timeout_ms=5000,
            )
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)


        # Confirmacao e Senha
        campo_senha = page.locator('[id="form:senha"]').first
        try:
            await campo_senha.wait_for(state="visible", timeout=8000)
            await campo_senha.fill(cfg.senha)
        except Exception:
            await preencher_primeiro_visivel(
                page,
                ["input[id*='senha']", "input[type='password']"],
                cfg.senha,
            )

        if args.executar:
            confirmar_btn = page.locator('[id="form:botaoConfirmarRegistro"]').first
            try:
                await confirmar_btn.wait_for(state="visible", timeout=5000)
                await confirmar_btn.click()
            except Exception:
                await clicar_primeiro_visivel(
                    page,
                    [
                        "input[id*='botaoConfirmarRegistro']",
                        "input[value='Confirmar']",
                        "button:has-text('Confirmar')",
                    ],
                )

            await page.wait_for_timeout(3000)
            conteudo = norm(await page.locator("body").inner_text())
            if "sucesso" in conteudo and "realizada" in conteudo:
                print("[OK] Matrícula de TCC executada — mensagem de sucesso detectada.")
            else:
                print("[WARN] Confirmacao clicada, mas mensagem de sucesso nao foi identificada no corpo da pagina.")
        else:
            print("[DRY-RUN] Parado antes do clique em 'Confirmar'. Senha preenchida mas NAO enviada.")

        if args.manter_aberto:
            print("[INFO] Navegador mantido aberto. Pressione Enter para fechar...")
            input()

        await context.close()
        await browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automacao SIGAA para matricular discente em TCC I"
    )
    parser.add_argument("--matricula", required=True, help="Matricula do aluno")
    parser.add_argument("--periodo", required=True, help="Periodo academico (ex: 2026.1)")
    parser.add_argument("--polo", required=True, help="Polo (usado para selecionar curso)")
    parser.add_argument("--componente", required=True, help="TCC I, TCC, TCC II")
    parser.add_argument("--orientador", required=True, help="Nome do orientador")
    parser.add_argument("--curso", default=None, help="Texto do curso no dropdown (sobrescreve --polo)")
    parser.add_argument("--atividade-nome", default=None, help="Nome exato/parcial da atividade para selecionar")
    parser.add_argument("--executar", action="store_true", help="Executa confirmacao final")
    parser.add_argument("--headless", action="store_true", help="Executa sem UI")
    parser.add_argument("--manter-aberto", action="store_true", help="Mantem navegador aberto no final")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(executar_fluxo_direto(args))


if __name__ == "__main__":
    main()
