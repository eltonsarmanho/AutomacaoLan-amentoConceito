"""
sigga_Consolidar_TCC.py — Consolida matrícula em TCC I no SIGAA.

Fluxo:
  [1/8]  Abrir navegador e SIGAA
  [2/8]  Login
  [3/8]  Selecionar período acadêmico
  [4/8]  Abrir Portal Coord. Graduação + selecionar curso/polo
  [5/8]  Menu Matrículas > Consolidar Matrículas
  [6/8]  Na lista de matrículas, localizar a linha do aluno em `TRABALHO DE CONCLUSAO DE CURSO I` e clicar na seta
  [7/8]  Selecionar Conceito "E" e clicar em `Próximo Passo >>`
  [8/8]  Clicar em `Confirmar` na tela "Dados do Registro" e aguardar "sucesso"

Uso:
  python sigga_Consolidar_TCCI.py \
      --matricula 202285940020 \
      --periodo 2026.1 \
      --polo "OEIRAS DO PARA" \
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
    "TCC": "TRABALHO DE CONCLUSAO DE CURSO",
    "TCC I": "TRABALHO DE CONCLUSAO DE CURSO I",
    "TCC II": "TRABALHO DE CONCLUSAO DE CURSO II",
}
CONCEITO_PADRAO = "E"

# ── Data classes ───────────────────────────────────────────────────────────────

class ConfigError(RuntimeError):
    pass


@dataclass
class ConfigSigaa:
    login: str
    senha: str
    sigaa_url: str


@dataclass
class EntradaDados:
    matricula: str
    periodo: str
    polo: str
    componente: str
    curso: str | None = None
    executar: bool = True
    headless: bool = False
    manter_aberto: bool = False


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
    faltando = [n for n, v in {"LOGIN": login, "SENHA": senha, "SIGAA_URL": sigaa_url}.items() if not v]
    if faltando:
        raise ConfigError(f"Variaveis obrigatorias faltando no .env: {', '.join(faltando)}")
    return ConfigSigaa(login=login, senha=senha, sigaa_url=sigaa_url)


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


# ── Navegação helpers ──────────────────────────────────────────────────────────

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


# ── Menu JSCookMenu: Atividades > Consolidar Matrículas ───────────────────────

async def _clicar_menu_atividades_consolidar(page) -> bool:
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

    # ---------- encontrar posicao do td "Atividades" ----------
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
        print("   [ERRO] td 'Atividades' nao encontrado na pagina.")
        return False

    print(f"   [DEBUG] Atividades posicao: x={ativ_box['x']:.0f}, y={ativ_box['y']:.0f}")

    ITEM_PARTIAL = "Consolidar Matr"

    for tentativa in range(3):
        print(f"   [TENTATIVA {tentativa+1}/3] Hover Atividades + buscar Consolidar Matriculas...")

        # 1. Hover sobre Atividades → JSCookMenu mostra submenu
        await page.mouse.move(ativ_box['x'], ativ_box['y'])
        await page.wait_for_timeout(800 + tentativa * 300)

        # 2. Encontrar "Consolidar Matrículas" no submenu
        item_info = await page.evaluate("""(partial) => {
            const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
            const candidates = [];
            for (const tr of trs) {
                const text = tr.textContent.trim();
                if (text.includes(partial)) {
                    const rect = tr.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0
                        && rect.left >= 0 && rect.top >= 0
                        && rect.right <= window.innerWidth
                        && rect.bottom <= window.innerHeight) {
                        const div = tr.closest('div');
                        const style = div ? getComputedStyle(div) : null;
                        const divVisible = !style
                            || (style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && parseFloat(style.opacity) > 0);
                        candidates.push({
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            top: rect.top, left: rect.left,
                            width: rect.width, height: rect.height,
                            divVisible, text,
                        });
                    }
                }
            }
            const visible = candidates.filter(c => c.divVisible);
            return visible.length > 0 ? visible[0]
                 : candidates.length > 0 ? candidates[0]
                 : null;
        }""", ITEM_PARTIAL)

        if not item_info:
            # Mover para baixo do td para explorar submenu
            await page.mouse.move(ativ_box['x'], ativ_box['bottom'] + 10)
            await page.wait_for_timeout(500)
            item_info = await page.evaluate("""(partial) => {
                const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
                for (const tr of trs) {
                    if (tr.textContent.trim().includes(partial)) {
                        const rect = tr.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.top > 0) {
                            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                                    top: rect.top, left: rect.left, divVisible: true, text: tr.textContent.trim()};
                        }
                    }
                }
                return null;
            }""", ITEM_PARTIAL)

        if not item_info or not item_info.get('divVisible'):
            print(f"   [INFO] Tentativa {tentativa+1}: 'Consolidar Matriculas' nao visivel (divVisible={item_info.get('divVisible') if item_info else None}). Re-hover...")
            await page.mouse.move(0, 0)
            await page.wait_for_timeout(500)
            continue

        print(f"   [DEBUG] '{item_info.get('text')}' encontrado: "
              f"x={item_info['x']:.0f}, y={item_info['y']:.0f}, divVisible={item_info.get('divVisible')}")

        # 3. Mover mouse gradualmente ate o item (manter hover ativo)
        target_x = item_info['x']
        target_y = item_info['y']
        mid_y = ativ_box.get('bottom', ativ_box['y'] + 15) + 5
        await page.mouse.move(ativ_box['x'], mid_y, steps=3)
        await page.wait_for_timeout(150)
        await page.mouse.move(target_x, mid_y, steps=3)
        await page.wait_for_timeout(150)
        await page.mouse.move(target_x, target_y, steps=5)
        await page.wait_for_timeout(200)

        # 4. Click real
        try:
            async with page.expect_navigation(timeout=8000):
                await page.mouse.click(target_x, target_y)
        except Exception as click_err:
            if _is_navigation_error(click_err):
                await page.wait_for_timeout(2000)
            else:
                print(f"   [WARN] Click falhou na tentativa {tentativa+1}: {click_err}")
                continue

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)
        body = (await page.locator("body").inner_text()).lower()
        if "consolidar" in body or "lista de matr" in body:
            print(f"   [OK] Menu Atividades > Consolidar Matriculas navegou! URL={page.url}")
            return True
        print(f"   [WARN] Tentativa {tentativa+1}: pagina nao e de consolidacao. URL={page.url}")

    # FALLBACK
    print("   [FALLBACK] Enumerando itens do submenu Atividades...")
    await page.mouse.move(ativ_box['x'], ativ_box['y'])
    await page.wait_for_timeout(800)

    submenu_items = await page.evaluate("""() => {
        const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
        const items = [];
        for (const tr of trs) {
            const text = tr.textContent.trim();
            const rect = tr.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && rect.top > 0
                && rect.right <= window.innerWidth && rect.bottom <= window.innerHeight) {
                items.push({text, x: rect.x + rect.width/2, y: rect.y + rect.height/2});
            }
        }
        return items;
    }""")

    if submenu_items:
        print(f"   [DEBUG] Itens visiveis no submenu ({len(submenu_items)}):")
        for item in submenu_items[:15]:
            print(f"      - '{item['text']}' em ({item['x']:.0f}, {item['y']:.0f})")

        target = None
        for item in submenu_items:
            txt = item['text'].strip().lower()
            if 'consolidar' in txt and 'matr' in txt:
                target = item
                break

        if target:
            print(f"   [DEBUG] Clicando em '{target['text']}' via fallback...")
            await page.mouse.move(ativ_box['x'], ativ_box['y'])
            await page.wait_for_timeout(600)
            await page.mouse.move(target['x'], target['y'], steps=10)
            await page.wait_for_timeout(200)
            try:
                async with page.expect_navigation(timeout=8000):
                    await page.mouse.click(target['x'], target['y'])
            except Exception as e:
                if _is_navigation_error(e):
                    await page.wait_for_timeout(2000)
                else:
                    print(f"   [WARN] Fallback click falhou: {e}")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)
            body = (await page.locator("body").inner_text()).lower()
            if "consolidar" in body or "lista de matr" in body:
                print(f"   [OK] Fallback funcionou! URL={page.url}")
                return True
            print(f"   [WARN] Fallback navegou mas nao e pagina de consolidacao.")

    print("   [ERRO] Todas as estrategias falharam para Atividades > Consolidar Matriculas.")
    return False


# ── Selecionar discente+componente na lista de consolidação ────────────────────

async def _selecionar_discente_componente(page, matricula: str, componente_nome: str) -> bool:
    await page.wait_for_timeout(1000)

    try:
        debug = await page.evaluate("""(params) => {
            const {matricula, componente} = params;
            const trs = document.querySelectorAll('tr');
            const info = {total_rows: trs.length, headers: [], matchingRows: []};
            let currentComponent = '';
            for (const tr of trs) {
                const text = tr.textContent.trim();
                const tds = tr.querySelectorAll('td');
                if (tds.length <= 2 && text.length > 5 && /^[A-Z\s]+[IVX]*$/.test(text)) {
                    currentComponent = text;
                    info.headers.push(text);
                    continue;
                }
                if (text.includes(matricula)) {
                    const seta = tr.querySelector('input[type=image], a:has(img[src*=seta]), img[src*=seta]');
                    const links = tr.querySelectorAll('a, input[type=image]');
                    info.matchingRows.push({
                        component: currentComponent,
                        text: text.substring(0, 120),
                        hasSeta: !!seta,
                        setaTag: seta ? seta.tagName : null,
                        setaSrc: seta ? seta.getAttribute('src') : null,
                        linksCount: links.length,
                    });
                }
            }
            return info;
        }""", {"matricula": matricula, "componente": componente_nome})
        print(f"   [DEBUG] Tabela consolidacao: {json.dumps(debug, indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"   [DEBUG] Falha ao debugar tabela: {e}")

    clicked = False
    try:
        result = await page.evaluate("""(params) => {
            const {matricula, componente} = params;
            const compNorm = componente.toUpperCase().trim();
            const trs = document.querySelectorAll('tr');
            let currentComponent = '';

            for (const tr of trs) {
                const text = tr.textContent.trim();
                const tds = tr.querySelectorAll('td');

                if (tds.length <= 2 && text.length > 5) {
                    const t = text.toUpperCase().trim();
                    if (/^[A-Z\s\u00C0-\u00FF]+[IVX]*$/.test(t) && !t.includes('MATR')) {
                        currentComponent = t;
                    }
                }

                if (text.includes(matricula) && currentComponent.includes(compNorm.substring(0, 20))) {
                    if (currentComponent === compNorm
                        || currentComponent.includes(compNorm)
                        || compNorm.includes(currentComponent)) {
                        const seta = tr.querySelector('input[type=image][src*=seta]')
                                  || tr.querySelector('a img[src*=seta]')
                                  || tr.querySelector('input[type=image]');
                        if (seta) {
                            const target = (seta.tagName === 'IMG' && seta.parentElement.tagName === 'A')
                                ? seta.parentElement : seta;
                            const rect = target.getBoundingClientRect();
                            return {
                                found: true,
                                component: currentComponent,
                                x: rect.x + rect.width/2,
                                y: rect.y + rect.height/2,
                                tag: target.tagName,
                            };
                        }
                        const links = tr.querySelectorAll('a');
                        if (links.length > 0) {
                            const last = links[links.length - 1];
                            const rect = last.getBoundingClientRect();
                            return {
                                found: true,
                                component: currentComponent,
                                x: rect.x + rect.width/2,
                                y: rect.y + rect.height/2,
                                tag: 'A-fallback',
                            };
                        }
                    }
                }
            }
            return {found: false};
        }""", {"matricula": matricula, "componente": componente_nome})

        if result and result.get("found"):
            print(f"   [DEBUG] Linha encontrada sob '{result.get('component')}', "
                  f"tag={result.get('tag')}, pos=({result['x']:.0f}, {result['y']:.0f})")

            linhas = page.locator(f"tr:has-text('{matricula}')")
            count = await linhas.count()
            for i in range(count):
                linha = linhas.nth(i)
                text_linha = await linha.inner_text()
                seta = linha.locator("input[type='image']").first
                if await seta.count():
                    url_antes = page.url
                    try:
                        async with page.expect_navigation(timeout=10000):
                            await seta.click(timeout=5000)
                    except Exception as click_err:
                        if _is_navigation_error(click_err) or page.url != url_antes:
                            return True
                        await page.wait_for_timeout(2000)
                        if page.url != url_antes:
                            return True
                    else:
                        return True

            url_antes = page.url
            try:
                async with page.expect_navigation(timeout=10000):
                    await page.mouse.click(result['x'], result['y'])
            except Exception as e:
                if _is_navigation_error(e) or page.url != url_antes:
                    return True
                await page.wait_for_timeout(2000)
                if page.url != url_antes:
                    return True
            else:
                return True

    except Exception as e:
        if _is_navigation_error(e):
            return True

    try:
        linha = page.locator(f"tr:has-text('{matricula}')").first
        if await linha.count():
            seta = linha.locator("input[type='image']").first
            if await seta.count():
                url_antes = page.url
                try:
                    async with page.expect_navigation(timeout=10000):
                        await seta.click(timeout=5000)
                except Exception as e:
                    if _is_navigation_error(e) or page.url != url_antes:
                        return True
                else:
                    return True
    except Exception as e:
        if _is_navigation_error(e):
            return True

    return False


# ── Fluxo principal ────────────────────────────────────────────────────────────

async def executar_consolidacao(args: argparse.Namespace) -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as err:
        raise RuntimeError(
            "Playwright nao encontrado. Instale com: pip install playwright && playwright install chromium"
        ) from err

    cfg = ler_config_env()
    entrada = EntradaDados(
        matricula=args.matricula,
        periodo=args.periodo,
        polo=args.polo,
        componente=args.componente,
        curso=getattr(args, "curso", None),
        executar=getattr(args, "executar", True),
        headless=getattr(args, "headless", False),
        manter_aberto=getattr(args, "manter_aberto", False),
    )

    componente_upper = entrada.componente.strip().upper()
    if componente_upper not in MAPA_COMPONENTE:
        raise ValueError(f"Componente inválido: {entrada.componente}")

    atividade_nome = MAPA_COMPONENTE[componente_upper]

    base = base_sigaa_url(cfg.sigaa_url)

    print("[1/8] Abrindo navegador e SIGAA...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=entrada.headless)
        context = await browser.new_context()
        page = await context.new_page()

        # ── LOGIN ──────────────────────────────────────────────────────────
        print("[2/8] Login...")
        await page.goto(cfg.sigaa_url, wait_until="domcontentloaded")

        login_field = page.locator("input[name='user.login']").first
        await login_field.wait_for(state="visible", timeout=10000)
        await login_field.fill(cfg.login)

        senha_field = page.locator("input[name='user.senha']").first
        await senha_field.fill(cfg.senha)

        submit = page.locator("input[type='submit']").first
        await submit.click()
        await page.wait_for_load_state("domcontentloaded")

        # ── PERÍODO ────────────────────────────────────────────────────────
        print("[3/8] Selecionando periodo academico...")
        await page.wait_for_load_state("domcontentloaded")
        if "calendarios" in page.url or "verTelaLogin" not in page.url:
            clicou_periodo = False
            for variacao in variacoes_periodo(entrada.periodo):
                try:
                    link_per = page.locator(f"a:has-text('{variacao}')").first
                    await link_per.wait_for(state="visible", timeout=3000)
                    await link_per.click()
                    await page.wait_for_load_state("domcontentloaded")
                    clicou_periodo = True
                    print(f"   [OK] Periodo '{variacao}' selecionado.")
                    break
                except Exception:
                    continue
            if not clicou_periodo:
                try:
                    link_qualquer = page.locator(
                        "table a[href*='verMenuPrincipal'], table a[href*='calendario'], "
                        "td.destaque a, td a[href*='periodo']"
                    ).first
                    await link_qualquer.wait_for(state="visible", timeout=3000)
                    await link_qualquer.click()
                    await page.wait_for_load_state("domcontentloaded")
                    print(f"   [WARN] Periodo '{entrada.periodo}' nao encontrado; clicou no primeiro disponivel.")
                except Exception:
                    print(f"   [WARN] Nao foi possivel clicar em periodo; tentando continuar.")

        # ── PORTAL COORD. GRADUAÇÃO ────────────────────────────────────────
        print("[4/8] Abrindo Portal Coord. Graduacao...")
        if "coordenador.jsf" not in page.url:
            link_portal = page.locator("a[href*='verPortalCoordenadorGraduacao']").first
            try:
                await link_portal.wait_for(state="visible", timeout=5000)
                await link_portal.click()
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                portal_url = f"{base}/sigaa/verPortalCoordenadorGraduacao.do"
                await page.goto(portal_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("domcontentloaded")

        if "coordenador.jsf" not in page.url:
            raise RuntimeError(f"Nao abriu Portal Coord. Graduacao. URL: {page.url}")

        # ── SELEÇÃO DE CURSO ───────────────────────────────────────────────
        print("[5/8] Selecionando curso pelo polo...")
        await page.wait_for_load_state("domcontentloaded")
        alvo_curso = entrada.curso if entrada.curso else entrada.polo
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
        else:
            print(f"   [WARN] Opcao de curso com '{alvo_curso}' nao encontrada. Seguindo com curso atual.")

        # ── MENU: Atividades > Consolidar Matrículas ──────────────────────
        print("[6/8] Menu Atividades > Consolidar Matriculas...")
        if not await _clicar_menu_atividades_consolidar(page):
            try:
                await page.screenshot(path="/tmp/sigaa_consolidar_tcci_menu_fail.png")
            except Exception:
                pass
            raise RuntimeError("Nao foi possivel navegar Atividades > Consolidar Matriculas.")

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        # ── SELECIONAR DISCENTE + COMPONENTE NA LISTA ──────────────────────
        print(f"[7/8] Selecionando discente {entrada.matricula} em '{atividade_nome}'...")
        if not await _selecionar_discente_componente(page, entrada.matricula, atividade_nome):
            try:
                await page.screenshot(path="/tmp/sigaa_consolidar_tcci_discente_fail.png")
            except Exception:
                pass
            raise RuntimeError(
                f"Nao encontrou matricula {entrada.matricula} sob componente '{atividade_nome}' na lista."
            )

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1000)

        # ── SELECIONAR CONCEITO ────────────────────────────────────────────
        print(f"[-] Selecionando Conceito '{CONCEITO_PADRAO}'...")

        conceito_selecionado = False
        for sel_id in ["select[id*='conceito' i]", "select[id*='resultado' i]",
                       "select[name*='conceito' i]", "select[name*='resultado' i]",
                       "select[id*='Conceito']", "select[id*='Resultado']"]:
            try:
                sel = page.locator(sel_id).first
                if await sel.count():
                    try:
                        await sel.select_option(label=CONCEITO_PADRAO, timeout=3000)
                        conceito_selecionado = True
                        break
                    except Exception:
                        pass
                    try:
                        await sel.select_option(value=CONCEITO_PADRAO, timeout=3000)
                        conceito_selecionado = True
                        break
                    except Exception:
                        pass
            except Exception:
                continue

        if not conceito_selecionado:
            conceito_selecionado = await selecionar_opcao_em_dropdown(
                page, CONCEITO_PADRAO, filtro_dropdown=None
            )

        if not conceito_selecionado:
            print(f"   [WARN] Nao foi possivel selecionar conceito '{CONCEITO_PADRAO}' automaticamente.")
        else:
            print(f"   [OK] Conceito '{CONCEITO_PADRAO}' selecionado.")

        # ── PROXIMO PASSO ──────────────────────────────────────────────────
        print("[8/8] Clicando 'Proximo Passo'...")
        clicou_prox = await clicar_primeiro_visivel(
            page,
            [
                "input[id*='btnConfirmacao']",
                "input[value*='Próximo Passo']",
                "input[value*='Proximo Passo']",
                "input[type='submit'][value*='ximo']",
                "button:has-text('Próximo')",
            ],
            timeout_ms=5000,
        )
        if not clicou_prox:
            try:
                await page.evaluate("""() => {
                    const btn = document.getElementById('form:btnConfirmacao');
                    if (btn) btn.click();
                }""")
                clicou_prox = True
                print("   [OK] 'Proximo Passo' clicado via JS.")
            except Exception:
                pass

        if not clicou_prox:
            print("   [WARN] Botao 'Proximo Passo' nao encontrado. Tentando continuar...")
        else:
            print("   [OK] 'Proximo Passo' clicado.")

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        # ── CONFIRMAÇÃO ────────────────────────────────────────────
        print("[-] Confirmacao final...")

        if entrada.executar:
            # Clicar em Confirmar/Consolidar
            clicou = await clicar_primeiro_visivel(
                page,
                [
                    "input[type='submit'][value*='Confirmar']",
                    "input[type='submit'][value*='Consolidar']",
                    "input[id*='confirmar' i]",
                    "input[id*='Confirmar']",
                    "input[id*='btnConfirmacao']",
                    "button:has-text('Confirmar')",
                    "button:has-text('Consolidar')",
                    "input[type='submit'][value*='Registrar']",
                ],
                timeout_ms=5000,
            )
            if not clicou:
                print("   [WARN] Nao achou botao 'Confirmar'. Verificando se já não consolidou.")

            # Aguardar resposta final de confirmacao
            await page.wait_for_timeout(3000)
            conteudo = norm(await page.locator("body").inner_text())
            if "sucesso" in conteudo or "consolidada" in conteudo or "realizada" in conteudo:
                print("[OK] Consolidacao de TCC I executada — mensagem de sucesso detectada.")
            else:
                print("[WARN] Confirmacao clicada, mas mensagem de sucesso nao identificada no corpo da pagina.")
                print(f"   [DEBUG] Primeiros 300 chars: {conteudo[:300]}")
        else:
            print("[DRY-RUN] Parado antes de Confirmar. NAO enviada a confirmacao final.")

        if entrada.manter_aberto:
            print("[INFO] Navegador mantido aberto. Pressione Enter para fechar...")
            input()

        await context.close()
        await browser.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Consolida matricula em TCC I no SIGAA"
    )

    parser.add_argument(
        "--componente",
        required=True,
        choices=["TCC", "TCC I", "TCC II"],
        help="Tipo de TCC (ex: 'TCC', 'TCC I', 'TCC II')",
    )

    parser.add_argument("--matricula", required=True, help="Matricula do aluno")
    parser.add_argument("--periodo", required=True, help="Periodo academico (ex: 2026.1)")
    parser.add_argument("--polo", required=True, help="Polo (usado para selecionar curso)")
    parser.add_argument("--curso", default=None, help="Texto do curso no dropdown (sobrescreve --polo)")
    parser.add_argument("--executar", action="store_true", help="Executa confirmacao final")
    parser.add_argument("--headless", action="store_true", help="Executa sem abrir navegador")
    parser.add_argument("--manter-aberto", action="store_true", help="Mantem navegador aberto no final")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(executar_consolidacao(args))


if __name__ == "__main__":
    main()
