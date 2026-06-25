from lancamento_service import LancamentoService
matricula_aluno = "202285940002"
polo_aluno= "OEIRAS DO PARÁ"
periodo_aluno='2026.3'
componente_aluno = ["ACC I", "ACC II", "ACC III", "ACC IV"]
for componente in componente_aluno:
        svc = LancamentoService(
                matricula=matricula_aluno,
                polo=polo_aluno,
                periodo=periodo_aluno,
                componente=componente,
        )

        resultado = svc.matricular_sync()
        if resultado.sucesso:
                print("Matrícula bem-sucedida!")
        else:
                print("Erro na matrícula.")

        resultado = svc.consolidar_sync(conceito="E")

