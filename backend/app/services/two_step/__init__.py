"""Two-step verification: a second proof after the password.

- ``state``           the per-user row
- ``totp``            authenticator-app codes
- ``recovery_codes``  one-time codes for a lost device
- ``passkeys``        WebAuthn credentials
- ``tickets``         short-lived proof that the password was right
- ``attempts``        wrong tries, and the pause after too many
- ``status``          what an account has; whether the server requires it
- ``sign_in``         session, second step or setup, for a proven password
- ``reset``           remove everything an account has
- ``clock``           the time, in one place so tests can set it
"""
