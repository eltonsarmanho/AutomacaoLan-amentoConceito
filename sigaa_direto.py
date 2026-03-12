import argparse
import asyncio
import json
import os
import re
import unicodedata
from urllib.parse import urlparse
from dataclasses import dataclass

from dotenv import load_dotenv


COMPONENTES_VALIDOS = {"ACC I", "ACC II","ACC III","ACC IV", "TCC I", "TCC II"}
MAPA_COMPONENTE = {
    "ACC I": ("ATIVIDADES COMPLEMENTARES", "ATIVIDADES CURRICULARES COMPLEMENTARES I"),
    "ACC II": ("ATIVIDADES COMPLEMENTARES", "ATIVIDADES CURRICULARES COMPLEMENTARES II"),
    "ACC III": ("ATIVIDADES COMPLEMENTARES", "ATIVIDADES CURRICULARES COMPLEMENTARES III"),
    "ACC IV": ("ATIVIDADES COMPLEMENTARES", "ATIVIDADES COMPLEMENTARES IV"),
    "TCC I": ("TRABALHO DE CONCLUSÃO DE CURSO", "TRABALHO DE CONCLUSAO DE CURSO I"),
}


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


async def marcar_checkbox_por_rotulo(page, rotulo_regex: str) -> bool:
    checkbox_por_label = page.locator(
        f"label:has-text('{rotulo_regex}') >> xpath=preceding::input[@type='checkbox'][1]"
    ).first
    try:
        await checkbox_por_label.wait_for(state="visible", timeout=2000)
        if not await checkbox_por_label.is_checked():
            await checkbox_por_label.check(force=True)
        return True
    except Exception:
        pass

    try:
        checkbox = page.locator(
            f"xpath=//tr[.//*[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]]//input[@type='checkbox'][1]"
        ).first
        await checkbox.wait_for(state="visible", timeout=2000)
        if not await checkbox.is_checked():
            await checkbox.check(force=True)
        return True
    except Exception:
        return False


async def preencher_input_por_rotulo(page, rotulo_regex: str, valor: str) -> bool:
    candidatos = [
        # Estrutura comum do SIGAA: rotulo na coluna esquerda e input na mesma linha
        f"xpath=//tr[.//*[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]]//input[not(@type='checkbox') and not(@type='hidden')][1]",
        f"xpath=//td[contains(translate(normalize-space(.), 'ÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ', 'AAAAEEEIIIOOOOUUUC'), '{rotulo_regex.upper()}')]/following::input[not(@type='checkbox') and not(@type='hidden')][1]",
    ]
    for seletor in candidatos:
        loc = page.locator(seletor).first
        try:
            await loc.wait_for(state="visible", timeout=2000)
            await loc.fill(valor)
            return True
        except Exception:
            continue
    return False


async def clicar_texto(page, texto: str, timeout_ms: int = 7000) -> bool:
    candidatos = [
        page.get_by_role("link", name=re.compile(re.escape(texto), re.IGNORECASE)).first,
        page.get_by_role("button", name=re.compile(re.escape(texto), re.IGNORECASE)).first,
        page.get_by_text(re.compile(re.escape(texto), re.IGNORECASE)).first,
    ]
    for loc in candidatos:
        try:
            await loc.wait_for(state="visible", timeout=timeout_ms)
            await loc.click()
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
    """Clica no link/imagem de selecao do discente na tabela de resultados.
    
    Em SIGAA, a selecao e tipicamente feita por um link <a> contendo uma <img>
    de seta/selecao, posicionado na ULTIMA coluna da linha do aluno.
    """
    # Debug: mostrar todos os links na pagina relacionados a selecao
    try:
        debug = await page.evaluate("""(matricula) => {
            const info = {allRowsWithMat: [], allSelectLinks: []};
            // 1. Linhas com a matricula
            const trs = document.querySelectorAll('tr');
            for (const tr of trs) {
                if (tr.textContent.includes(matricula)) {
                    const links = tr.querySelectorAll('a, input[type=image], img');
                    info.allRowsWithMat.push({
                        text: tr.textContent.trim().substring(0, 150),
                        linksCount: links.length,
                        links: [...links].map(l => ({
                            tag: l.tagName,
                            href: l.getAttribute('href'),
                            onclick: (l.getAttribute('onclick') || '').substring(0, 200),
                            alt: l.getAttribute('alt'),
                            src: l.getAttribute('src'),
                            title: l.getAttribute('title'),
                            text: l.textContent.trim().substring(0, 50),
                        }))
                    });
                }
            }
            // 2. Todos os links/imgs que parecem ser de selecao
            document.querySelectorAll('a img, input[type=image], a[onclick*=submit], a[onclick*=jsfcljs]').forEach(el => {
                info.allSelectLinks.push({
                    tag: el.tagName,
                    src: el.getAttribute('src'),
                    alt: el.getAttribute('alt'),
                    parentOnclick: el.parentElement ? (el.parentElement.getAttribute('onclick') || '').substring(0, 200) : null,
                    parentHref: el.parentElement ? el.parentElement.getAttribute('href') : null,
                });
            });
            return info;
        }""", matricula)
        print(f"   [DEBUG] Selecao discente: {json.dumps(debug, indent=2, ensure_ascii=False)}")
    except Exception as dbg_err:
        print(f"   [DEBUG] Falha ao debugar selecao: {dbg_err}")

    # 1) Preferencia: na linha com a matricula, clicar no input[type=image] (seta de selecao)
    # A seta de selecao e um <input type="image" src="seta.gif"> — elemento correto!
    # O <a onclick="habilitarDetalhes(...)"> e apenas "Visualizar Detalhes" e NAO seleciona.
    linha = page.locator(f"tr:has-text('{matricula}')").first
    try:
        if await linha.count():
            # Debug: mostrar info do form e do input
            try:
                input_debug = await page.evaluate("""(matricula) => {
                    const trs = document.querySelectorAll('tr');
                    for (const tr of trs) {
                        if (!tr.textContent.includes(matricula)) continue;
                        const inp = tr.querySelector('input[type=image]');
                        if (!inp) continue;
                        const form = inp.closest('form');
                        return {
                            inputName: inp.name, inputId: inp.id,
                            inputSrc: inp.src, inputTitle: inp.title,
                            formId: form ? form.id : null,
                            formAction: form ? form.action : null,
                            formMethod: form ? form.method : null,
                            formOnsubmit: form ? form.getAttribute('onsubmit') : null,
                            hiddenCount: form ? form.querySelectorAll('input[type=hidden]').length : 0,
                            viewState: form ? (form.querySelector('input[name="javax.faces.ViewState"]') || {}).value : null,
                        };
                    }
                    return null;
                }""", matricula)
                print(f"   [DEBUG] Input seta info: {json.dumps(input_debug, indent=2, ensure_ascii=False)}")
            except Exception:
                pass

            # Prioridade 1: input[type='image'] (a seta de selecao)
            seta = linha.locator("input[type='image']").first
            if await seta.count():
                print(f"   [DEBUG] Clicando em input[type=image] (seta) na linha da matricula")
                url_antes = page.url
                try:
                    async with page.expect_navigation(timeout=10000):
                        await seta.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err) or page.url != url_antes:
                        print(f"   [DEBUG] Apos click seta: URL mudou de {url_antes} para {page.url}")
                        return True
                    print(f"   [WARN] Click seta sem navegacao: {click_err}")
                    # Tentar esperar mais
                    await page.wait_for_timeout(3000)
                    if page.url != url_antes:
                        print(f"   [DEBUG] Apos espera extra: URL mudou para {page.url}")
                        return True
                    # Nao deu certo, mas continua tentando outros metodos
                else:
                    print(f"   [DEBUG] Apos click seta: URL = {page.url}")
                    return True

            # Prioridade 2: link com imagem de seta
            seta_link = linha.locator("a:has(img[src*='seta'])").first
            if await seta_link.count():
                print(f"   [DEBUG] Clicando em a:has(img seta) na linha da matricula")
                try:
                    await seta_link.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True

            # Prioridade 3: link com jsfcljs (JSF command link de submit)
            jsf_link = linha.locator("a[onclick*='jsfcljs']").first
            if await jsf_link.count():
                print(f"   [DEBUG] Clicando em a[onclick*=jsfcljs] na linha da matricula")
                try:
                    await jsf_link.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True

            # Prioridade 4: ultimo <a> na linha (nao o primeiro, que e 'Visualizar Detalhes')
            all_links = linha.locator("a")
            count = await all_links.count()
            if count > 0:
                alvo = all_links.nth(count - 1)
                print(f"   [DEBUG] Clicando no ultimo <a> (de {count}) na linha da matricula")
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
        print(f"   [WARN] Falha ao clicar na linha com matricula: {e}")

    # 2) Linha com FORMANDO
    linha_formando = page.locator("tr:has-text('FORMANDO')").first
    try:
        if await linha_formando.count():
            # Prioridade 1: input[type='image'] (seta)
            seta = linha_formando.locator("input[type='image']").first
            if await seta.count():
                print(f"   [DEBUG] Clicando em input[type=image] (seta) na linha FORMANDO")
                try:
                    await seta.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True

            # Prioridade 2: link com jsfcljs
            jsf_link = linha_formando.locator("a[onclick*='jsfcljs']").first
            if await jsf_link.count():
                print(f"   [DEBUG] Clicando em a[onclick*=jsfcljs] na linha FORMANDO")
                try:
                    await jsf_link.click(timeout=5000)
                except Exception as click_err:
                    if _is_navigation_error(click_err):
                        return True
                    raise
                return True

            # Prioridade 3: ultimo <a>
            all_links = linha_formando.locator("a")
            count = await all_links.count()
            if count > 0:
                alvo = all_links.nth(count - 1)
                print(f"   [DEBUG] Clicando no ultimo <a> (de {count}) na linha FORMANDO")
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
        print(f"   [WARN] Falha ao clicar na linha FORMANDO: {e}")

    # 3) Fallback generico
    print("   [DEBUG] Usando fallback generico para selecao de discente")
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
    """Retorna True se a excecao indica que uma navegacao ocorreu (contexto destruido)."""
    msg = str(exc).lower()
    return any(k in msg for k in [
        "execution context was destroyed",
        "most likely because of a navigation",
        "navigating",
        "target page, context or browser has been closed",
        "frame was detached",
    ])


async def _check_navegou(page, fragmento_url: str, timeout_ms: int = 5000) -> bool:
    """Verifica se a pagina ja navegou para a URL esperada, aguardando se necessario."""
    if fragmento_url in page.url:
        return True
    try:
        await page.wait_for_url(f"**{fragmento_url}**", timeout=timeout_ms)
        return True
    except Exception:
        return fragmento_url in page.url


async def _aguardar_navegacao(page, esperado_na_url: str, timeout_ms: int = 10000) -> bool:
    """Aguarda até a URL conter o fragmento esperado."""
    try:
        await page.wait_for_url(f"**{esperado_na_url}**", timeout=timeout_ms)
        return True
    except Exception:
        return esperado_na_url in page.url


async def _clicar_menu_atividades_matricular(page) -> bool:
    """
    Navega pelo menu bar ThemeOffice (JSCookMenu) do SIGAA: Atividades > Matricular.

    DESCOBERTA CRITICA (via debug dump):
      - Os elementos do menu NAO possuem onclick/onmouseover como atributos HTML.
      - Os event handlers sao adicionados via addEventListener pelo JSCookMenu.
      - Portanto element.click() em JS (isTrusted=false) NAO aciona os handlers.
      - SOMENTE eventos reais de mouse do Playwright (page.mouse.*) funcionam.
      - Forcar display:block via JS CORROMPE o estado interno do JSCookMenu.

    Abordagem correta:
      1. page.mouse.move() sobre 'Atividades' → JSCookMenu mostra o submenu
      2. Encontrar coordenadas do item 'Matricular' no submenu visivel
      3. page.mouse.move() ate 'Matricular' (passando pelo submenu para manter hover)
      4. page.mouse.click() no 'Matricular'
    """

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
        print("   [ERRO] td 'Atividades' nao encontrado na pagina.")
        return False

    print(f"   [DEBUG] Atividades posicao: x={ativ_box['x']:.0f}, y={ativ_box['y']:.0f}")

    # ====================================================================
    # ESTRATEGIA PRINCIPAL: Mouse real hover + click (evento confiavel)
    # ====================================================================
    for tentativa in range(3):
        print(f"   [TENTATIVA {tentativa+1}/3] Mouse hover em Atividades + click em Matricular...")

        # 1. Hover sobre Atividades → JSCookMenu mostra submenu
        await page.mouse.move(ativ_box['x'], ativ_box['y'])
        await page.wait_for_timeout(800 + tentativa * 300)

        # 2. Verificar se algum submenu ficou visivel e encontrar 'Matricular'
        matricular_info = await page.evaluate("""() => {
            // Procura TODAS as trs com classe ThemeOfficeMenuItem
            const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
            const candidates = [];
            for (const tr of trs) {
                const text = tr.textContent.trim();
                // Match exato "Matricular" — exclui "Desmatricular", "Matriculados...", etc.
                if (text === 'Matricular') {
                    const rect = tr.getBoundingClientRect();
                    // Verificar se esta realmente visivel (na viewport e com dimensoes)
                    if (rect.width > 0 && rect.height > 0
                        && rect.left >= 0 && rect.top >= 0
                        && rect.right <= window.innerWidth
                        && rect.bottom <= window.innerHeight) {
                        // Verificar se o div pai esta visivel
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
                            divId: div ? div.id : null,
                            divDisplay: style ? style.display : null,
                            divVisibility: style ? style.visibility : null,
                            divVisible: divVisible,
                        });
                    }
                }
            }
            // Se encontrou candidatos visiveis, retorna o primeiro
            const visible = candidates.filter(c => c.divVisible);
            return visible.length > 0 ? visible[0]
                 : candidates.length > 0 ? candidates[0]
                 : null;
        }""")

        if not matricular_info:
            print(f"   [INFO] Tentativa {tentativa+1}: 'Matricular' nao visivel apos hover. Tentando explorar submenus...")
            # O submenu 'Atividades' pode ter sub-submenus. Mover o mouse para baixo
            # do td para entrar na area do submenu
            await page.mouse.move(ativ_box['x'], ativ_box['bottom'] + 10)
            await page.wait_for_timeout(500)
            # Tentar novamente
            matricular_info = await page.evaluate("""() => {
                const trs = document.querySelectorAll('tr.ThemeOfficeMenuItem');
                for (const tr of trs) {
                    if (tr.textContent.trim() === 'Matricular') {
                        const rect = tr.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && rect.top > 0) {
                            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2,
                                    top: rect.top, left: rect.left, divVisible: true};
                        }
                    }
                }
                return null;
            }""")

        if not matricular_info:
            print(f"   [INFO] Tentativa {tentativa+1}: Ainda nao encontrou. Tentando re-hover...")
            # Mover mouse para fora e voltar (reset do menu)
            await page.mouse.move(0, 0)
            await page.wait_for_timeout(500)
            continue

        print(f"   [DEBUG] Matricular encontrado: x={matricular_info['x']:.0f}, y={matricular_info['y']:.0f}, divVisible={matricular_info.get('divVisible')}")

        # 3. Mover mouse de Atividades ATE Matricular, passando pelo submenu
        # Movimentacao gradual para manter o hover ativo no submenu
        current_x = ativ_box['x']
        current_y = ativ_box['y']
        target_x = matricular_info['x']
        target_y = matricular_info['y']

        # Mover primeiro na vertical (descendo para o submenu), depois ajustar horizontal
        # Isso evita sair da area do submenu
        mid_y = ativ_box.get('bottom', ativ_box['y'] + 15) + 5
        await page.mouse.move(current_x, mid_y, steps=3)
        await page.wait_for_timeout(150)
        await page.mouse.move(target_x, mid_y, steps=3)
        await page.wait_for_timeout(150)
        await page.mouse.move(target_x, target_y, steps=5)
        await page.wait_for_timeout(200)

        # 4. Click real no Matricular
        try:
            async with page.expect_navigation(timeout=8000):
                await page.mouse.click(target_x, target_y)
            if "busca_discente" in page.url:
                print("   [OK] Menu Atividades > Matricular navegou com sucesso!")
                return True
        except Exception as click_err:
            if _is_navigation_error(click_err) or "busca_discente" in page.url:
                if await _check_navegou(page, "busca_discente", 5000):
                    print("   [OK] Menu Atividades > Matricular navegou (detected via error)!")
                    return True
            print(f"   [WARN] Click falhou na tentativa {tentativa+1}: {click_err}")

    # ====================================================================
    # FALLBACK: Tentar clicar em cada item do submenu Atividades via mouse
    # (pode ser que o texto exato nao seja "Matricular" mas algo similar)
    # ====================================================================
    print("   [FALLBACK] Tentando todos os itens do submenu Atividades...")
    # Hover de novo em Atividades
    await page.mouse.move(ativ_box['x'], ativ_box['y'])
    await page.wait_for_timeout(800)

    # Obter todos os itens visiveis do submenu
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
        for item in submenu_items[:10]:
            print(f"      - '{item['text']}' em ({item['x']:.0f}, {item['y']:.0f})")

        # Procurar item que contenha "Matricular" mas NAO "Desmatricular" ou "Matriculados"
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
            print(f"   [DEBUG] Clicando em '{target['text']}' via fallback...")
            # Re-hover em Atividades e navegar ate o target
            await page.mouse.move(ativ_box['x'], ativ_box['y'])
            await page.wait_for_timeout(600)
            await page.mouse.move(target['x'], target['y'], steps=10)
            await page.wait_for_timeout(200)
            try:
                async with page.expect_navigation(timeout=8000):
                    await page.mouse.click(target['x'], target['y'])
                if "busca_discente" in page.url:
                    print("   [OK] Fallback funcionou!")
                    return True
            except Exception as e:
                if _is_navigation_error(e):
                    if await _check_navegou(page, "busca_discente", 5000):
                        print("   [OK] Fallback funcionou (navigation detected)!")
                        return True
                print(f"   [WARN] Fallback click falhou: {e}")

    # ====================================================================
    # ULTIMO RECURSO: Procurar na pagina JS do JSCookMenu a acao de Matricular
    # e executar via JSF form submit
    # ====================================================================
    print("   [ULTIMO RECURSO] Procurando JSCookMenu config e JSF hidden params...")
    try:
        jsf_info = await page.evaluate("""() => {
            const result = {};
            // 1. Listar scripts que definem o menu
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const src = s.textContent || '';
                if (src.includes('cmDraw') || src.includes('ThemeOffice')) {
                    // Procurar a definicao do item Matricular
                    const match = src.match(/['"](Matricular)['"]\s*,\s*['"]([^'"]+)['"]/);
                    if (match) {
                        result.menuAction = match[2];
                    }
                    // Procurar qualquer URL de busca_discente
                    const urlMatch = src.match(/busca_discente[^'"]*\.jsf/);
                    if (urlMatch) {
                        result.discUrl = urlMatch[0];
                    }
                    // Pegar trecho relevante
                    const idx = src.indexOf('Matricular');
                    if (idx >= 0) {
                        result.menuContext = src.substring(Math.max(0, idx-200), idx+200);
                    }
                }
            }
            // 2. Verificar JSCookMenu globals
            if (typeof cmDraw === 'function') result.hasCmDraw = true;
            if (typeof cmItemMouseDown === 'function') result.hasCmItemMouseDown = true;
            // 3. Listar hidden inputs do form
            const form = document.querySelector('form');
            if (form) {
                result.formId = form.id;
                result.formAction = form.action;
                const hiddens = form.querySelectorAll('input[type=hidden]');
                result.hiddens = [...hiddens].slice(0, 20).map(h => ({name: h.name, value: h.value.substring(0, 100)}));
            }
            return result;
        }""")
        print(f"   [DEBUG] JSF/JSCookMenu info: {json.dumps(jsf_info, indent=2, ensure_ascii=False)}")

        # Se encontrou a acao do menu, tentar executar
        if jsf_info.get('menuContext'):
            # Tentar extrair e executar o handler de Matricular do JSCookMenu
            exec_result = await page.evaluate("""(ctx) => {
                // Procurar nos scripts a funcao associada a Matricular
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const src = s.textContent || '';
                    const idx = src.indexOf('Matricular');
                    if (idx < 0) continue;
                    // JSCookMenu format: [icon, text, action, target, ...]
                    // Try to find the action (usually a javascript: URL or function call)
                    // Look for the string before and after "Matricular"
                    const region = src.substring(Math.max(0, idx-500), idx+100);
                    // Look for function call pattern like "jsfcljs(...)"
                    const fnMatch = region.match(/(jsfcljs|submitForm|document\\.forms[^;]+;)/);
                    if (fnMatch) {
                        try { eval(fnMatch[0]); return 'evaled:' + fnMatch[0]; } catch(e) {}
                    }
                    // Look for 'javascript:' URL
                    const jsMatch = region.match(/javascript:([^'"\\]]+)/);
                    if (jsMatch) {
                        try { eval(jsMatch[1]); return 'evaled_js:' + jsMatch[1]; } catch(e) {}
                    }
                }
                return null;
            }""", jsf_info.get('menuContext', ''))
            if exec_result:
                print(f"   [INFO] JSCookMenu exec: {exec_result}")
                if await _check_navegou(page, "busca_discente", 5000):
                    print("   [OK] Ultimo recurso funcionou!")
                    return True
    except Exception as e:
        if _is_navigation_error(e):
            if await _check_navegou(page, "busca_discente", 5000):
                print("   [OK] Ultimo recurso funcionou (navigation detected)!")
                return True
        print(f"   [WARN] Ultimo recurso falhou: {e}")

    print("   [ERRO] Todas as estrategias falharam para Menu Atividades > Matricular.")
    print(f"   [DEBUG] URL atual: {page.url}")
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
        # Confirmado no mapeamento step 2-3: campos name="user.login" e name="user.senha"
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

        # --- SELECAO DE PERIODO NO CALENDARIO (step 4 do mapeamento) ---
        # Apos o login, SIGAA redireciona para calendarios_graduacao_vigentes.jsf.
        # E necessario clicar no link do periodo academico para ativar o contexto
        # de semestre. Sem esse passo, o dropdown de cursos no coordenador.jsf fica vazio.
        print("[3/9] Selecionando periodo academico no calendario...")
        await page.wait_for_load_state("domcontentloaded")
        if "calendarios" in page.url or "verTelaLogin" not in page.url:
            # Tenta clicar no link do periodo (ex: "2026.1" ou "2026-1") dentro da pagina
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
                    print(f"   [OK] Periodo '{variacao}' selecionado.")
                    break
                except Exception:
                    continue

            if not clicou_periodo:
                # Fallback: clica no primeiro link de periodo disponivel na tabela
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
                    print(f"   [WARN] Periodo '{entrada.periodo}' nao encontrado; clicou no primeiro periodo disponivel.")
                except Exception:
                    print(f"   [WARN] Nao foi possivel clicar em periodo; tentando continuar.")

        # --- PORTAL COORD. GRADUACAO (step 5 do mapeamento) ---
        # Apos selecionar o periodo, o link do portal aparece no menu principal.
        # href="/sigaa/verPortalCoordenadorGraduacao.do" confirmado no step 5.
        print("[3/9] Abrindo Portal Coord. Graduacao...")
        if "coordenador.jsf" not in page.url:
            link_portal = page.locator("a[href*='verPortalCoordenadorGraduacao']").first
            try:
                await link_portal.wait_for(state="visible", timeout=5000)
                await link_portal.click()
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                # Fallback via URL direta (so funciona se o contexto de periodo ja estiver ativo)
                portal_do_url = f"{base}/sigaa/verPortalCoordenadorGraduacao.do"
                await page.goto(portal_do_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("domcontentloaded")

        if "coordenador.jsf" not in page.url:
            raise RuntimeError(f"Nao foi possivel abrir o Portal Coord. Graduacao. URL atual: {page.url}")

        # --- SELECAO DE CURSO ---
        # Confirmado no mapeamento step 6-7: dropdown na pagina coordenador.jsf.
        # A opcao real observada: "SISTEMAS DE INFORMACAO - OEIRAS/CCAME - OEIRAS DO PARÁ"
        # Selecionamos pela correspondencia parcial com o polo.
        # O dropdown tem onchange="submit()" portanto a pagina recarrega automaticamente.
        print("[4/9] Selecionando curso pelo polo...")
        await page.wait_for_load_state("domcontentloaded")
        alvo_curso = args.curso if args.curso else entrada.polo
        selecionou = await selecionar_opcao_em_dropdown(page, alvo_curso)
        if selecionou:
            # Aguarda o reload provocado pelo onchange="submit()".
            # IMPORTANTE: domcontentloaded nao basta — pode resolver no documento antigo.
            # Precisamos esperar a pagina nova carregar completamente.
            try:
                await page.wait_for_load_state("load", timeout=10000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)  # margem extra p/ JS do ThemeOffice inicializar
        else:
            print(f"[WARN] Opcao de curso com '{alvo_curso}' nao encontrada. Seguindo com curso atual.")

        # --- MENU ATIVIDADES > MATRICULAR ---
        # Confirmado no mapeamento step 8-9:
        #   step 8: click no elemento DENTRO de td.ThemeOfficeMainItem 'Atividades' (idx=15, nao idx=14)
        #   step 9: click no elemento DENTRO de tr.ThemeOfficeMenuItem 'Matricular' (idx=38, nao idx=37)
        # Resultado: navega para busca_discente.jsf
        print("[5/9] Menu Atividades > Matricular...")
        if not await _clicar_menu_atividades_matricular(page):
            # Ultima tentativa: screenshot e dump do HTML para debug
            try:
                await page.screenshot(path="/tmp/sigaa_menu_fail.png")
                print("   [DEBUG] Screenshot salvo em /tmp/sigaa_menu_fail.png")
            except Exception:
                pass
            raise RuntimeError("Nao foi possivel navegar pelo menu Atividades > Matricular.")

        # Aguarda navegacao com tentativas
        navegou = await _aguardar_navegacao(page, "busca_discente.jsf", timeout_ms=10000)
        if not navegou:
            # Pode ser que o clique acionou um reload; esperar um pouco mais
            await page.wait_for_timeout(2000)
            navegou = "busca_discente.jsf" in page.url
        if not navegou:
            raise RuntimeError(f"Esperava busca_discente.jsf, mas URL e: {page.url}")

        # --- BUSCA DO DISCENTE ---
        # Confirmado no mapeamento step 10:
        #   click em input[id="formulario:checkMatricula"] (radio/checkbox de criterio matricula)
        #   fill em input[id="formulario:matriculaDiscente"]
        #   click em input[id="formulario:buscar"]
        print("[6/9] Buscando aluno por matricula...")
        await page.wait_for_load_state("domcontentloaded")

        check_mat = page.locator('[id="formulario:checkMatricula"]').first
        try:
            await check_mat.wait_for(state="visible", timeout=5000)
            await check_mat.click()
        except Exception:
            print("[WARN] Checkbox formulario:checkMatricula nao encontrado; tentando fallback...")
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
        await page.wait_for_timeout(1000)  # margem extra para resultados renderizarem

        # Debug: verificar se a tabela de resultados apareceu
        try:
            rows_debug = await page.evaluate("""() => {
                const trs = document.querySelectorAll('table tr');
                const info = [];
                for (const tr of trs) {
                    const text = tr.textContent.trim().substring(0, 100);
                    if (text.includes('2022') || text.includes('FORMANDO') || text.includes('ATIVO')
                        || text.includes('Matrícula') || text.includes('matricula')) {
                        const links = tr.querySelectorAll('a, input[type=image], img[alt]');
                        info.push({
                            text: text.substring(0, 80),
                            linksCount: links.length,
                            linkTags: [...links].map(l => l.tagName + (l.href ? '[href]' : '') + (l.alt ? '[alt='+l.alt+']' : '')).slice(0, 5)
                        });
                    }
                }
                return info;
            }""")
            if rows_debug:
                print(f"   [DEBUG] Linhas relevantes na tabela: {json.dumps(rows_debug[:5], ensure_ascii=False)}")
            else:
                print("   [DEBUG] Nenhuma linha com matricula encontrada. Conteudo da pagina pode estar carregando...")
        except Exception as dbg_err:
            print(f"   [DEBUG] Falha ao debugar resultados: {dbg_err}")

        # --- SELECAO DO DISCENTE ---
        # Confirmado no mapeamento step 11: click em idx=46 (link na linha do aluno)
        # A linha contem a matricula; clicamos no primeiro link/imagem disponivel
        print("[7/9] Selecionando discente na lista...")
        url_antes = page.url
        if not await clicar_seta_selecao_discente(page, entrada.matricula):
            # Tentar aguardar mais e repetir
            await page.wait_for_timeout(2000)
            if not await clicar_seta_selecao_discente(page, entrada.matricula):
                try:
                    await page.screenshot(path="/tmp/sigaa_discente_fail.png")
                    print("   [DEBUG] Screenshot salvo em /tmp/sigaa_discente_fail.png")
                except Exception:
                    pass
                raise RuntimeError("Nao foi possivel selecionar o discente na lista de resultados.")

        # Aguardar navegacao apos clicar no discente
        # O click pode demorar; esperamos com margem
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
            # Pode ser que o clique acionou um reload; esperar um pouco mais
            await page.wait_for_timeout(3000)
            navegou = "busca_atividade.jsf" in page.url

        # JSF pode fazer forward (mesma URL, conteudo diferente) em vez de redirect.
        # Verificar se a pagina agora mostra o formulario de busca de atividade
        # (select de tipo de atividade, botao "Buscar Atividades", etc.)
        if not navegou:
            try:
                page_check = await page.evaluate("""() => {
                    const info = {
                        url: window.location.href,
                        title: document.title,
                        headings: [...document.querySelectorAll('h2, h3, .titulo, .tituloFormulario')]
                            .map(h => h.textContent.trim().substring(0, 80)).slice(0, 5),
                        forms: [...document.querySelectorAll('form')].map(f => ({
                            id: f.id,
                            action: f.action,
                            inputs: [...f.querySelectorAll('input, select, button')].slice(0, 10).map(i => ({
                                tag: i.tagName, id: i.id, name: i.name, type: i.type,
                                value: (i.value || '').substring(0, 50)
                            }))
                        })),
                        hasAtividadeDropdown: !!document.querySelector('[id*="idTipoAtividade"]'),
                        hasBuscarAtividades: !!document.querySelector('[id*="atividades"]'),
                        hasDiscenteInfo: !!document.querySelector('[id*="discente"], [id*="Discente"]'),
                        errorMsgs: [...document.querySelectorAll('.erros, .error, .msgErro, .alert-danger, li.error')]
                            .map(e => e.textContent.trim().substring(0, 100)),
                        bodyText: document.body ? document.body.textContent.substring(0, 500) : '',
                    };
                    return info;
                }""")
                print(f"   [DEBUG] Pagina apos click seta: {json.dumps(page_check, indent=2, ensure_ascii=False)}")

                # Se a pagina tem o dropdown de tipo de atividade, estamos na pagina certa
                if page_check.get('hasAtividadeDropdown') or page_check.get('hasBuscarAtividades'):
                    print("   [OK] Pagina de busca de atividade detectada (JSF forward, URL nao mudou)")
                    navegou = True
            except Exception as check_err:
                print(f"   [DEBUG] Falha ao verificar conteudo da pagina: {check_err}")

        if not navegou:
            # Se a URL mudou, pode ter ido para outro lugar
            if page.url != url_antes:
                print(f"   [WARN] URL mudou para {page.url}, nao e busca_atividade.jsf. Tentando continuar...")
            else:
                try:
                    await page.screenshot(path="/tmp/sigaa_discente_nav_fail.png")
                    print("   [DEBUG] Screenshot salvo em /tmp/sigaa_discente_nav_fail.png")
                except Exception:
                    pass
                raise RuntimeError(f"Esperava busca_atividade.jsf, mas URL e: {page.url}")

        # --- SELECAO DE ATIVIDADE ---
        # Confirmado no mapeamento step 12:
        #   click em checkbox/radio de filtro (idx=39)
        #   select em select[id="form:idTipoAtividade"] → "ATIVIDADES COMPLEMENTARES"
        #   click em input[id="form:atividades"] (Buscar Atividades)
        # Steps 13-20: agente buscou e clicou no link da atividade na tabela de resultados
        # Step 21: clicou em botao de confirmacao (idx=37, nao idx=35 que e "Selecionar Outra Atividade")
        #          para ir a dados_registro.jsf
        print("[8/9] Selecionando tipo de atividade e buscando componente...")
        await page.wait_for_load_state("domcontentloaded")

        # 8a. Selecionar tipo de atividade no dropdown
        sel_tipo = page.locator('[id="form:idTipoAtividade"]').first
        try:
            await sel_tipo.wait_for(state="visible", timeout=5000)
            await sel_tipo.select_option(label=tipo_atividade)
        except Exception:
            if not await selecionar_opcao_em_dropdown(page, tipo_atividade, filtro_dropdown="tipoAtividade"):
                raise RuntimeError(f"Nao foi possivel selecionar tipo de atividade: {tipo_atividade}")

        # 8b. Clicar em Buscar Atividades
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

        # 8c. Localiza a linha da atividade pelo nome e clica no link de selecao
        # Tentar varias formas de encontrar a atividade na tabela de resultados
        encontrou_atividade = False
        for tentativa in range(2):
            # Tenta pelo nome da atividade
            for texto_busca in [atividade_nome, atividade_nome.upper(), atividade_nome.split(" - ")[-1] if " - " in atividade_nome else atividade_nome]:
                linha = page.locator(f"tr:has-text('{texto_busca}')").first
                if await linha.count():
                    clicou = False
                    for sel in ["a", "input[type='image']", "img", "input[type='submit']"]:
                        alvo_el = linha.locator(sel).first
                        if await alvo_el.count():
                            try:
                                await alvo_el.click(timeout=3000)
                                clicou = True
                                break
                            except Exception:
                                continue
                    if clicou:
                        encontrou_atividade = True
                        break
            if encontrou_atividade:
                break

            # Se nao encontrou na 1a tentativa, tenta preencher campo de codigo e buscar novamente
            if tentativa == 0:
                print("   [INFO] Atividade nao encontrada na 1a busca, tentando com campo de codigo...")
                # Buscar campo de codigo (geralmente input text apos os checkboxes/radios)
                campo_codigo = page.locator("input[id*='codigoComponente'], input[id*='codigo'], input[id*='nomeComponente'], input[id*='nome']").first
                try:
                    if await campo_codigo.count():
                        await campo_codigo.fill(atividade_nome.split(" - ")[0].strip() if " - " in atividade_nome else atividade_nome)
                        buscar_ativ2 = page.locator('[id="form:atividades"]').first
                        await buscar_ativ2.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(1000)
                except Exception:
                    pass

        if not encontrou_atividade:
            raise RuntimeError(f"Atividade '{atividade_nome}' nao encontrada na tabela de resultados.")

        # 8d. Apos selecionar a atividade, a pagina mostra confirmacao.
        # Clicar no botao de confirmacao para prosseguir a dados_registro.jsf.
        # ATENCAO: idx=35 e "Selecionar Outra Atividade" (form:btnAtividades) — NAO clicar neste!
        # O botao correto e outro submit (idx=37 no mapeamento step 21).
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(500)

        if "dados_registro.jsf" not in page.url:
            clicou_proximo = await clicar_primeiro_visivel(
                page,
                [
                    # Botoes de confirmacao comuns (exclui "Selecionar Outra Atividade")
                    "input[type='submit'][value*='Confirmar']:not([id*='btnAtividades'])",
                    "input[type='submit'][value*='Próximo']:not([id*='btnAtividades'])",
                    "input[type='submit'][value*='Proximo']:not([id*='btnAtividades'])",
                    "input[type='submit'][value*='Registrar']:not([id*='btnAtividades'])",
                    "button:has-text('Confirmar')",
                    "button:has-text('Próximo')",
                    # Fallback: qualquer submit que NAO seja btnAtividades ou form:atividades
                    "input[type='submit']:not([id='form:btnAtividades']):not([id='form:atividades'])",
                ],
                timeout_ms=5000,
            )
            if clicou_proximo:
                await page.wait_for_timeout(500)
                await _aguardar_navegacao(page, "dados_registro.jsf", timeout_ms=8000)

        if "dados_registro.jsf" not in page.url:
            raise RuntimeError(f"Esperava dados_registro.jsf, mas URL e: {page.url}")

        # --- SENHA E CONFIRMACAO ---
        # Confirmado no mapeamento step 22:
        #   input[id="form:senha"]                 → campo de senha
        #   input[id="form:botaoConfirmarRegistro"] → botao Confirmar
        print("[9/9] Senha e confirmacao final...")
        await page.wait_for_load_state("domcontentloaded")

        campo_senha = page.locator('[id="form:senha"]').first
        try:
            await campo_senha.wait_for(state="visible", timeout=8000)
            await campo_senha.fill(cfg.senha)
        except Exception:
            if not await preencher_primeiro_visivel(
                page,
                ["input[id*='senha']", "input[type='password']"],
                cfg.senha,
            ):
                raise RuntimeError("Nao foi possivel preencher a senha de confirmacao.")

        if args.executar:
            confirmar_btn = page.locator('[id="form:botaoConfirmarRegistro"]').first
            try:
                await confirmar_btn.wait_for(state="visible", timeout=5000)
                await confirmar_btn.click()
            except Exception:
                if not await clicar_primeiro_visivel(
                    page,
                    [
                        "input[id*='botaoConfirmarRegistro']",
                        "input[value='Confirmar']",
                        "button:has-text('Confirmar')",
                    ],
                ):
                    raise RuntimeError("Nao foi possivel clicar em Confirmar.")

            await page.wait_for_timeout(3000)
            conteudo = norm(await page.locator("body").inner_text())
            if "sucesso" in conteudo and ("matricula em atividade" in conteudo or "realizada com sucesso" in conteudo):
                print("[OK] Confirmacao executada — mensagem de sucesso detectada.")
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
        description="Automacao SIGAA por instrucoes diretas (sem LLM)"
    )
    parser.add_argument("--matricula", required=True, help="Matricula do aluno")
    parser.add_argument("--periodo", required=True, help="Periodo academico (ex: 2026.1)")
    parser.add_argument("--polo", required=True, help="Polo (usado para selecionar curso)")
    parser.add_argument("--componente", required=True, help="ACC I, ACC II, TCC I, TCC II")
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
