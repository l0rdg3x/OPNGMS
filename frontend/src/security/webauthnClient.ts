// Browser WebAuthn plumbing: converts the server's JSON options (py_webauthn
// `options_to_json` shape — base64url for every binary field) into the BufferSource
// shapes the WebAuthn API needs, calls the platform authenticator, and serialises the
// resulting credential back into the JSON the backend's verify_* helpers consume.

/**
 * Decode a base64url string into a fresh ArrayBuffer (a `BufferSource` the
 * WebAuthn API accepts directly for challenge / id fields).
 */
export function base64urlToBytes(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  const binary = atob(padded + pad);
  const buffer = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return buffer;
}

/** Encode an ArrayBuffer (or view) into a base64url string (no padding). */
export function bytesToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** True when the browser exposes the WebAuthn API. */
export function webauthnSupported(): boolean {
  return typeof window !== "undefined" && !!window.PublicKeyCredential;
}

// The server JSON is loosely typed (an opaque object); narrow as we read it.
type Json = Record<string, unknown>;

interface DescriptorJson {
  id: string;
  type: PublicKeyCredentialType;
  transports?: AuthenticatorTransport[];
}

function toDescriptors(
  list: unknown,
): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) return undefined;
  return (list as DescriptorJson[]).map((d) => ({
    id: base64urlToBytes(d.id),
    type: d.type,
    ...(d.transports ? { transports: d.transports } : {}),
  }));
}

/**
 * Run registration: decode the server options, call `navigator.credentials.create`,
 * and serialise the new credential into the standard WebAuthn JSON
 * (`id`, `rawId`, `type`, `response.{attestationObject,clientDataJSON}`, `transports`).
 */
export async function createCredential(optionsJson: Json): Promise<Json> {
  const o = optionsJson;
  const user = o.user as Json;
  const publicKey: PublicKeyCredentialCreationOptions = {
    challenge: base64urlToBytes(o.challenge as string),
    rp: o.rp as PublicKeyCredentialRpEntity,
    user: {
      id: base64urlToBytes(user.id as string),
      name: user.name as string,
      displayName: user.displayName as string,
    },
    pubKeyCredParams: o.pubKeyCredParams as PublicKeyCredentialParameters[],
    ...(o.timeout != null ? { timeout: o.timeout as number } : {}),
    ...(o.attestation ? { attestation: o.attestation as AttestationConveyancePreference } : {}),
    ...(o.authenticatorSelection
      ? { authenticatorSelection: o.authenticatorSelection as AuthenticatorSelectionCriteria }
      : {}),
    ...(toDescriptors(o.excludeCredentials)
      ? { excludeCredentials: toDescriptors(o.excludeCredentials) }
      : {}),
  };

  const credential = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("WebAuthn registration was cancelled");

  const response = credential.response as AuthenticatorAttestationResponse;
  const transports =
    typeof response.getTransports === "function" ? response.getTransports() : [];

  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    response: {
      attestationObject: bytesToBase64url(response.attestationObject),
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
      transports,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

/**
 * Run authentication: decode the server options, call `navigator.credentials.get`,
 * and serialise the assertion into the standard WebAuthn JSON
 * (`id`, `rawId`, `type`, `response.{authenticatorData,clientDataJSON,signature,userHandle}`).
 */
export async function getAssertion(optionsJson: Json): Promise<Json> {
  const o = optionsJson;
  const publicKey: PublicKeyCredentialRequestOptions = {
    challenge: base64urlToBytes(o.challenge as string),
    ...(o.timeout != null ? { timeout: o.timeout as number } : {}),
    ...(o.rpId ? { rpId: o.rpId as string } : {}),
    ...(o.userVerification
      ? { userVerification: o.userVerification as UserVerificationRequirement }
      : {}),
    ...(toDescriptors(o.allowCredentials)
      ? { allowCredentials: toDescriptors(o.allowCredentials) }
      : {}),
  };

  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!credential) throw new Error("WebAuthn authentication was cancelled");

  const response = credential.response as AuthenticatorAssertionResponse;

  return {
    id: credential.id,
    rawId: bytesToBase64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment ?? undefined,
    response: {
      authenticatorData: bytesToBase64url(response.authenticatorData),
      clientDataJSON: bytesToBase64url(response.clientDataJSON),
      signature: bytesToBase64url(response.signature),
      userHandle: response.userHandle ? bytesToBase64url(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}
