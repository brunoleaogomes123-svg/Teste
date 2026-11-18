# Tuzz - Controle de Estoque

## Executar localmente no Windows

Use Python 3.10 ou superior. No PowerShell, na pasta do projeto:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:SECRET_KEY = (& python -c "import secrets; print(secrets.token_hex(32))").Trim()
flask --app app create-admin
flask --app app run
```

Abra `http://127.0.0.1:5000`. A primeira execução cria o banco SQLite em
`instance/acuracidade.sqlite3`. O comando `create-admin` cria a única conta ADM;
use uma senha forte e não a compartilhe. O ADM cria as demais contas pela aba
**Usuários (ADM)** depois de entrar.

O segredo definido em `SECRET_KEY` deve ser mantido em ambiente seguro e
reutilizado após reiniciar o servidor. Não o coloque no código nem o envie ao
Git. Para produção, use HTTPS, defina `COOKIE_SECURE=true` e execute com um
servidor WSGI de produção; `flask run` é apenas para desenvolvimento local.
O limitador de tentativas usa memória por padrão para desenvolvimento. Em
produção, configure `RATELIMIT_STORAGE_URI` com um armazenamento compartilhado,
como Redis, para manter os limites entre processos.

## Recuperação de senha por e-mail

Configure o SMTP do seu provedor antes de iniciar o servidor. No PowerShell,
defina `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`
e `PUBLIC_BASE_URL` no ambiente. Exemplo local:

```powershell
$env:PUBLIC_BASE_URL = "http://127.0.0.1:5000"
$env:SMTP_HOST = "smtp.seu-provedor.com"
$env:SMTP_PORT = "587"
$env:SMTP_USERNAME = "seu-usuario"
$env:SMTP_PASSWORD = "sua-senha-de-aplicativo"
$env:SMTP_FROM = "acesso@seudominio.com"
$env:SMTP_USE_TLS = "true"
```

Use a senha de aplicativo fornecida pelo serviço de e-mail quando disponível.
O link de redefinição expira em 30 minutos e só pode ser usado uma vez. Sem a
configuração SMTP, o servidor informa que a recuperação não está disponível.

As contas e sessões são verificadas pelo servidor Python. Os dados do estoque
continuam usando o armazenamento local do navegador, como antes; eles ainda não
são sincronizados entre computadores ou isolados por usuário.
