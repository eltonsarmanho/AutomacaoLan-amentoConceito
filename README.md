# Automação de Lançamento de Conceito no SIGAA

Automação com [Playwright](https://playwright.dev/python/) para matrícula e consolidação de atividades complementares (ACC) e TCC no SIGAA, sem uso de LLM.

---

## Scripts

| Script | Função |
|---|---|
| `sigaa_Matricular.py` | Matricula um aluno em uma atividade (ACC/TCC) |
| `sigaa_Consolidar.py` | Consolida (lança conceito) uma matrícula existente |
| `processar_lote.py` | Executa matrícula + consolidação em lote para vários alunos |
| `main.py` | Fluxo alternativo via agente LLM (`browser-use`) — não recomendado |

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

## 2. `sigaa_Matricular.py` — Matricular aluno

Navega em **Atividades → Matricular** e registra o aluno no componente indicado.

### Componentes suportados

| Sigla | Componente no SIGAA |
|---|---|
| `ACC I` | ATIVIDADES CURRICULARES COMPLEMENTARES I |
| `ACC II` | ATIVIDADES CURRICULARES COMPLEMENTARES II |
| `ACC III` | ATIVIDADES CURRICULARES COMPLEMENTARES III |
| `ACC IV` | ATIVIDADES COMPLEMENTARES IV |
| `TCC I` | TRABALHO DE CONCLUSAO DE CURSO I |

### Uso

```bash
# Dry-run (não confirma — apenas verifica os passos)
python sigaa_Matricular.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I"

# Executar de verdade
python sigaa_Matricular.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I" \
  --headless \
  --executar
```

### Opções

| Flag | Descrição |
|---|---|
| `--matricula` | Matrícula do aluno (obrigatório) |
| `--periodo` | Período acadêmico, ex: `2026.1` (obrigatório) |
| `--polo` | Texto do polo para localizar o curso no dropdown (obrigatório) |
| `--componente` | `ACC I`, `ACC II`, `ACC III`, `ACC IV`, `TCC I` (obrigatório) |
| `--executar` | Confirma a operação (sem esta flag roda em dry-run) |
| `--headless` | Executa sem abrir janela do navegador |
| `--manter-aberto` | Mantém o navegador aberto ao final (útil para depuração) |
| `--curso "..."` | Sobrescreve o texto usado para localizar o curso no dropdown |
| `--atividade-nome "..."` | Sobrescreve o nome da atividade a selecionar |

---

## 3. `sigaa_Consolidar.py` — Consolidar matrícula

Navega em **Atividades → Consolidar Matrículas** e lança o conceito para o aluno.

### Uso

```bash
# Dry-run
python sigaa_Consolidar.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I"

# Executar (conceito padrão: E)
python sigaa_Consolidar.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I" \
  --headless \
  --executar

# Executar com conceito diferente
python sigaa_Consolidar.py \
  --matricula 202285940020 \
  --periodo 2026.1 \
  --polo "OEIRAS DO PARÁ" \
  --componente "ACC I" \
  --conceito S \
  --headless \
  --executar
```

### Opções

| Flag | Descrição |
|---|---|
| `--matricula` | Matrícula do aluno (obrigatório) |
| `--periodo` | Período acadêmico (obrigatório) |
| `--polo` | Polo para localizar o curso (obrigatório) |
| `--componente` | `ACC I` … `ACC IV`, `TCC I` (obrigatório) |
| `--conceito` | Conceito a atribuir — padrão: `E` |
| `--executar` | Confirma a operação |
| `--headless` | Sem interface gráfica |
| `--manter-aberto` | Mantém navegador aberto ao final |
| `--curso "..."` | Sobrescreve texto do curso no dropdown |

---

## 4. `processar_lote.py` — Processar em lote

Executa matrícula **e** consolidação para múltiplos alunos/componentes de forma sequencial.

### Configurar a lista

Edite a variável `LOTE` no topo do arquivo:

```python
LOTE: list[list] = [
    # [matricula, polo, periodo, componente]
    [202285640031, "Limoeiro do Ajuru", "2026.1", "ACC"],   # expande: ACC I, II, III, IV
    [202285940015, "Oeiras do Pará",    "2026.1", "ACC I"], # apenas ACC I
    [202285940020, "Oeiras do Pará",    "2026.1", "TCC"],   # expande: TCC I
]
```

**Atalhos de componente** — expansão automática:

| Atalho | Expande para |
|---|---|
| `ACC` | ACC I, ACC II, ACC III, ACC IV |
| `TCC` | TCC I |

### Uso

```bash
# Dry-run (padrão — não confirma nada)
python processar_lote.py

# Executar matrícula + consolidação
python processar_lote.py --executar

# Apenas matricular (sem consolidar)
python processar_lote.py --executar --so-matricular

# Apenas consolidar (alunos já matriculados)
python processar_lote.py --executar --so-consolidar

# Conceito diferente de E
python processar_lote.py --executar --conceito S

# Abrir o navegador (depuração)
python processar_lote.py --executar --sem-headless
```

### Opções

| Flag | Descrição |
|---|---|
| `--executar` | Confirma as operações (sem esta flag: dry-run) |
| `--sem-headless` | Abre o navegador visualmente |
| `--so-matricular` | Apenas matrícula, sem consolidar |
| `--so-consolidar` | Apenas consolidação, sem matricular |
| `--conceito` | Conceito para consolidação — padrão: `E` |

### Resumo ao final

```
======================================================================
  RESUMO FINAL — 14:32:07
======================================================================
  Matrícula      Polo                 Per.     Comp.    Matr.  Cons.
  ----------------------------------------------------------------
  202285640031   Limoeiro do Ajuru    2026.1   ACC I    ✅     ✅
  202285640031   Limoeiro do Ajuru    2026.1   ACC II   ✅     ✅
  202285940015   Oeiras do Pará       2026.1   ACC I    ⚠️     ⚠️
======================================================================
  ⚠️  1 entrada(s) já processadas (aluno já matriculado ou já integralizado).
  ✅ Nenhum erro crítico — lote concluído.
```

| Símbolo | Significado |
|---|---|
| ✅ | Executado com sucesso |
| ⚠️ | Falhou — aluno já matriculado / já integralizado (não é erro crítico) |
| ❌ | Erro inesperado (timeout, credencial inválida, etc.) |
| — | Etapa não executada (`--so-matricular` ou `--so-consolidar`) |

---

## Observações

- O script usa eventos de mouse confiáveis (`page.mouse.click`) para compatibilidade com o menu JSCookMenu do SIGAA.
- Mudanças visuais no SIGAA podem exigir ajuste de seletores nos scripts.
- Cada execução individual tem timeout de 5 minutos; a pausa entre entradas do lote é de 5 segundos.
