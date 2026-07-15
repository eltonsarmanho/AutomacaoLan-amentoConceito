# Automação de Lançamento de Conceito no SIGAA

Automação com [Playwright](https://playwright.dev/python/) para matrícula e consolidação de atividades complementares (ACC) e TCC no SIGAA, sem uso de LLM.

> **Arquitetura (desde 07/2026):** toda a lógica de navegação vive em
> `sigaa_core.py` (menu determinístico via `jscook_action`, matching estrito de
> componente, leitura das mensagens do SIGAA, retentativas e rastreamento
> automático em `rastreamento/`). Os scripts específicos de ACC e TCC são CLIs
> finos organizados em pastas. Detalhes em `RELATORIO_MUDANCAS.md`.

---

## Estrutura de Arquivos

### Raiz (lógica centralizada + interfaces)

| Script | Função |
|---|---|
| `sigaa_core.py` | Núcleo compartilhado: fluxos, navegação, robustez, rastreamento automático |
| `SIGAA_Main.py` | **Lote interativo** — principal entrada para operações em lote; pergunta matrículas, componente, período, polo, operação, conceito e orientador |
| `lancamento_service.py` | API síncrona/assíncrona para integração com Streamlit e serviços externos |
| `rastreador_sigaa.py` / `rastreador_tcc.py` | Rastreamento interativo (mapeamento manual de fluxos novos) |

### ACC/ — Matrícula e consolidação de ACC I..IV

| Script | Função |
|---|---|
| `sigaa_Matricular.py` | Matricula aluno em ACC I..IV |
| `sigaa_Consolidar.py` | Consolida (lança conceito) em ACC I..IV |

### TCC/ — Matrícula e consolidação de TCC I/II

| Script | Função |
|---|---|
| `sigaa_Matricular_TCC.py` | Matricula aluno em TCC I/II (orientador obrigatório) |
| `sigaa_Consolidar_TCC.py` | Consolida (lança conceito) em TCC I/II |

**Exit codes** de todos os CLIs: `0` sucesso · `3` já matriculado/consolidado (não crítico) · `2` erro real.

**Flags novas** em todos os CLIs: `--tentativas N` (retentativa automática, padrão 2) e `--sem-rastreio` (desliga screenshots/JSONL por execução).

---

## 1. Configuração

### Variáveis de ambiente — arquivo `.env`

```env
LOGIN=seu_login
SENHA=sua_senha
SIGAA_URL=https://sigaa.exemplo.edu.br/sigaa/verTelaLogin.do
```

Opcionais (apenas para `main.py` com LLM):

```env
MARITALK_API_KEY=...
GOOGLE_API_KEY=...
```

### Instalar dependências

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 2. SIGAA_Main.py — Interface interativa principal

Entrada interativa para operações em lote. Pergunta sequencialmente matrículas, componente (ACC/TCC), período, polo, operação (matrícula/consolidação), conceito e orientador (se TCC).

### Uso

```bash
# Modo interativo (pergunta tudo)
python SIGAA_Main.py

# Com flags opcionais
python SIGAA_Main.py --sem-headless --tentativas 3
```

### Opções

| Flag | Descrição |
|---|---|
| `--executar` | Confirma as operações (sem esta flag: dry-run) |
| `--sem-headless` | Abre o navegador visualmente |
| `--tentativas N` | Número de tentativas automáticas (padrão: 2) |
| `--sem-rastreio` | Desativa screenshots/JSONL por execução |

---

## 3. ACC/ — Scripts para ACC I..IV

### ACC/sigaa_Matricular.py — Matricular aluno

Navega em **Atividades → Matricular** e registra o aluno em ACC.

#### Componentes suportados

| Sigla | Componente no SIGAA |
|---|---|
| `ACC I` | ATIVIDADES CURRICULARES COMPLEMENTARES I |
| `ACC II` | ATIVIDADES CURRICULARES COMPLEMENTARES II |
| `ACC III` | ATIVIDADES CURRICULARES COMPLEMENTARES III |
| `ACC IV` | ATIVIDADES COMPLEMENTARES IV |

#### Uso

```bash
# Dry-run (não confirma — apenas verifica os passos)
python ACC/sigaa_Matricular.py \
  --matricula 202116040015 \
  --periodo 2026.2 \
  --polo "CAMETÁ" \
  --componente "ACC II"

# Executar de verdade
python ACC/sigaa_Matricular.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I" \
  --headless \
  --executar
```

#### Opções

| Flag | Descrição |
|---|---|
| `--matricula` | Matrícula do aluno (obrigatório) |
| `--periodo` | Período acadêmico (obrigatório) |
| `--polo` | Texto do polo para localizar o curso (obrigatório) |
| `--componente` | `ACC I`, `ACC II`, `ACC III`, `ACC IV` (obrigatório) |
| `--executar` | Confirma a operação (padrão: dry-run) |
| `--headless` | Executa sem interface gráfica |
| `--manter-aberto` | Mantém navegador aberto ao final (depuração) |
| `--tentativas N` | Retentativas automáticas (padrão: 2) |
| `--sem-rastreio` | Desativa rastreamento de execução |

---

### ACC/sigaa_Consolidar.py — Consolidar matrícula ACC

Navega em **Atividades → Consolidar Matrículas** e lança o conceito para o aluno em ACC.

#### Uso

```bash
# Dry-run
python ACC/sigaa_Consolidar.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I"

# Executar (conceito padrão: E)
python ACC/sigaa_Consolidar.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I" \
  --conceito E \
  --headless \
  --executar
```

#### Opções

| Flag | Descrição |
|---|---|
| `--matricula` | Matrícula do aluno (obrigatório) |
| `--periodo` | Período acadêmico (obrigatório) |
| `--polo` | Polo para localizar o curso (obrigatório) |
| `--componente` | `ACC I` … `ACC IV` (obrigatório) |
| `--conceito` | Conceito a atribuir: B, E, I, R, S (padrão: `E`) |
| `--executar` | Confirma a operação |
| `--headless` | Sem interface gráfica |
| `--manter-aberto` | Mantém navegador aberto |
| `--tentativas N` | Retentativas automáticas (padrão: 2) |

---

## 4. TCC/ — Scripts para TCC I/II

### TCC/sigaa_Matricular_TCC.py — Matricular aluno em TCC

Mesmo fluxo que ACC, mas com etapa obrigatória de **Orientador** (preenchido via autocomplete AJAX).

#### Uso

```bash
# Dry-run
python TCC/sigaa_Matricular_TCC.py \
  --matricula 202416040009 \
  --periodo 2026.3 \
  --polo "CAMETÁ" \
  --componente "TCC I" \
  --orientador "ELTON SARMANHO SIQUEIRA"

# Executar
python TCC/sigaa_Matricular_TCC.py \
  --matricula 202416040009 \
  --periodo 2026.3 \
  --polo "CAMETÁ" \
  --componente "TCC I" \
  --orientador "ELTON SARMANHO SIQUEIRA" \
  --headless \
  --executar
```

#### Opções

| Flag | Descrição |
|---|---|
| `--matricula` | Matrícula do aluno (obrigatório) |
| `--periodo` | Período acadêmico (obrigatório) |
| `--polo` | Polo para localizar o curso (obrigatório) |
| `--componente` | `TCC I`, `TCC II` (obrigatório) |
| `--orientador` | Nome do orientador (obrigatório para TCC) |
| `--executar` | Confirma a operação |
| `--headless` | Sem interface gráfica |
| `--manter-aberto` | Mantém navegador aberto |
| `--tentativas N` | Retentativas automáticas (padrão: 2) |

---

### TCC/sigaa_Consolidar_TCC.py — Consolidar matrícula TCC

Lança conceito para aluno em TCC I/II. Fluxo idêntico ao ACC, porém restrito a componentes TCC.

#### Uso

```bash
# Dry-run
python TCC/sigaa_Consolidar_TCC.py \
  --matricula 202416040009 \
  --periodo 2026.3 \
  --polo "CAMETÁ" \
  --componente "TCC I"

# Executar
python TCC/sigaa_Consolidar_TCC.py \
  --matricula 202416040009 \
  --periodo 2026.3 \
  --polo "CAMETÁ" \
  --componente "TCC I" \
  --conceito E \
  --headless \
  --executar
```

#### Opções

| Flag | Descrição |
|---|---|
| `--matricula` | Matrícula do aluno (obrigatório) |
| `--periodo` | Período acadêmico (obrigatório) |
| `--polo` | Polo para localizar o curso (obrigatório) |
| `--componente` | `TCC I`, `TCC II` (obrigatório) |
| `--conceito` | Conceito a atribuir: B, E, I, R, S (padrão: `E`) |
| `--executar` | Confirma a operação |
| `--headless` | Sem interface gráfica |
| `--tentativas N` | Retentativas automáticas (padrão: 2) |

---

## 5. API para Integração — lancamento_service.py

Para integrar com Streamlit ou outros serviços, use `LancamentoService`:

```python
from lancamento_service import LancamentoService

# ACC
svc = LancamentoService(
    matricula="202285940020",
    polo="OEIRAS DO PARÁ",
    periodo="2026.1",
    componente="ACC I",
)
resultado = svc.matricular_sync()
resultado = svc.consolidar_sync(conceito="E")

# TCC
svc_tcc = LancamentoService(
    matricula="202416040009",
    polo="CAMETÁ",
    periodo="2026.2",
    componente="TCC I",
    orientador="ELTON SARMANHO SIQUEIRA",
)
resultado = svc_tcc.matricular_sync()
resultado = svc_tcc.consolidar_sync(conceito="E")
```

---

## Observações

- **Exit codes**: `0` sucesso · `3` já matriculado/consolidado (não crítico) · `2` erro real
- **Rastreamento**: cada execução gera `rastreamento/<fluxo>_<matricula>_<comp>_<timestamp>/` com eventos JSONL e screenshots
- **JSCookMenu**: menu determinístico via `jscook_action` extraído dos scripts da página (não usa hover)
- **Component matching**: usa regex com lookahead para evitar "TCC I" casar com "TCC II"
- **Orientador (TCC)**: verificado via hidden `form:idOrientador` após autocomplete AJAX 