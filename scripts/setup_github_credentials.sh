#!/usr/bin/env bash
set -Eeuo pipefail
USERNAME="mmaximm2017-netizen"
HOST="github.com"
command -v git >/dev/null 2>&1 || { echo "git is required." >&2; exit 1; }
git config --global credential.helper store
read -rsp "GitHub token (input hidden): " TOKEN; printf '\n'
printf 'protocol=https\nhost=%s\nusername=%s\npassword=%s\n\n' "$HOST" "$USERNAME" "$TOKEN" | git credential approve
unset TOKEN
CREDENTIALS_FILE="${HOME}/.git-credentials"
[[ -f "$CREDENTIALS_FILE" ]] || { echo "Credential setup failed: $CREDENTIALS_FILE was not created." >&2; exit 1; }
chmod 600 "$CREDENTIALS_FILE"
git credential fill >/dev/null <<EOF
protocol=https
host=$HOST
username=$USERNAME

EOF
if ! GIT_TERMINAL_PROMPT=0 git push --dry-run origin main >/dev/null 2>&1; then
    echo "Credential verification failed: git push --dry-run still cannot authenticate." >&2; exit 1
fi
echo "GitHub credentials configured for $USERNAME@$HOST."
echo "The token is stored as plaintext in $CREDENTIALS_FILE (mode 600). Remove or revoke it when no longer needed."
