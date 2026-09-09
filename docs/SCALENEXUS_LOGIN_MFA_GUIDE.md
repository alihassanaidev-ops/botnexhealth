# ScaleNexus Login and MFA Guide

This short guide is intended for clinic staff and administrators.

## Sign in normally

1. Open the ScaleNexus login page and enter your own work email and password.
2. Enter the six-digit code shown in the authenticator app on your phone, or use
   your passkey if that is your preferred method.
3. If neither is to hand, choose **Use an email code instead** and we will send a
   six-digit code to your work email address.
4. Recovery codes are not part of normal sign-in. Use one only if your usual MFA
   method is unavailable.

Each staff member should use an individual account. Shared accounts make it hard
to identify who viewed or changed clinic information.

## Recommended setup for staff using multiple computers

Choose **Authenticator app (TOTP)** during setup. Scan the QR code using Google
Authenticator, Microsoft Authenticator, 1Password, Authy, or another compatible
app. The app remains on your phone, so the same six-digit code works when you
sign in from any clinic computer.

The code changes about every 30 seconds. Enter the code currently visible in the
app; you do not need to receive an email or text message.

## Passkeys

Passkeys are phishing-resistant and can use Touch ID, Face ID, Windows Hello, or
a security key. Some passkeys synchronize through an Apple, Google, Microsoft,
or password-manager account. Others remain tied to one device. If you regularly
move between clinic computers, confirm that your passkey is synchronized or keep
the authenticator app enabled as another sign-in method.

You can add more than one passkey from **Security** settings and give each one a
clear device name.

## Email codes

From your second sign-in onward, the verification screen offers **Use an email
code instead**. We email a six-digit code to your work address; enter it to
finish signing in. The code is different from your authenticator app code, works
once, and expires after ten minutes. If it does not arrive, wait a minute and
choose **Resend code**.

This option only appears after you have set up an authenticator app or a passkey
— it is a convenience for when your phone or passkey is out of reach, not a
replacement for setting one up. It also cannot be used to change your security
settings: adding or removing a passkey, disabling the authenticator app, or
regenerating recovery codes still requires your authenticator app, passkey, or a
recovery code. That way, someone who reaches your mailbox cannot remove the
methods protecting your account.

Because the code arrives in your mailbox, an email code is only as strong as your
email account. Keep your work email protected with its own MFA, and prefer the
authenticator app or a passkey for everyday sign-in.

## Recovery codes

Recovery codes are one-time emergency backups. Store them somewhere secure and
separate from the computer you use to sign in. Do not use or share them during
normal login. After a code is used, it cannot be used again.

If you lose both your normal MFA method and recovery codes, contact your
ScaleNexus administrator for an MFA reset. Your identity should be verified
before the reset is performed.

## Password and session rules

- New passwords must contain at least 12 characters, including uppercase and
  lowercase letters, a number, and a symbol.
- MFA remains required after the password step.
- An inactive browser session ends after eight hours. A warning appears one
  minute before logout; **Stay signed in** securely renews the server session.

## Managing sign-in methods

Open **Security** settings to add or remove passkeys, enable or disable an
authenticator app, or generate a fresh set of recovery codes. Adding, removing,
or regenerating a security factor requires another MFA check — and that check
does not accept an email code.

Email codes need no setup and appear on the sign-in screen automatically, so
there is nothing to manage for them here.
