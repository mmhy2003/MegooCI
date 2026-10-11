/**
 * Passkeys in the browser: the two prompts, and the reasons they may not be
 * usable on this page.
 */

import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
  WebAuthnError,
} from "@simplewebauthn/browser";
import type { PasskeyJson } from "./api";

type CreationOptions = Parameters<typeof startRegistration>[0]["optionsJSON"];
type RequestOptions = Parameters<typeof startAuthentication>[0]["optionsJSON"];

/**
 * Why passkeys cannot be used on this page, or null when they can.
 *
 * A passkey is tied to the server's public address (`passkeyOrigin`): opened
 * at any other address, the browser would refuse it. So does a browser
 * without passkey support, or a page that is not on HTTPS or localhost.
 */
export function passkeyUnavailableReason(passkeyOrigin: string): string | null {
  if (typeof window === "undefined") return null;
  if (window.location.origin !== passkeyOrigin) {
    return `Passkeys work only at ${passkeyOrigin}.`;
  }
  if (!window.isSecureContext || !browserSupportsWebAuthn()) {
    return "This browser cannot use passkeys here.";
  }
  return null;
}

/** Ask the browser to create a passkey. Resolves to its answer for the server. */
export async function createPasskey(options: PasskeyJson): Promise<PasskeyJson> {
  const answer = await startRegistration({
    optionsJSON: options as unknown as CreationOptions,
  });
  return answer as unknown as PasskeyJson;
}

/** Ask the browser to sign in with a passkey. Resolves to its answer for the server. */
export async function signInWithPasskey(options: PasskeyJson): Promise<PasskeyJson> {
  const answer = await startAuthentication({
    optionsJSON: options as unknown as RequestOptions,
  });
  return answer as unknown as PasskeyJson;
}

/**
 * True when the browser's own prompt was dismissed, timed out, or was not
 * allowed to open. Nothing was sent, so there is nothing to report: the
 * screen just stays as it is.
 */
export function passkeyPromptDismissed(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const cause = error instanceof WebAuthnError ? error.cause : undefined;
  const names = [error.name, cause instanceof Error ? cause.name : ""];
  return names.includes("NotAllowedError") || names.includes("AbortError");
}

/** True when the browser says this passkey is already registered to the account. */
export function passkeyAlreadyRegistered(error: unknown): boolean {
  return (
    error instanceof WebAuthnError &&
    error.code === "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED"
  );
}
