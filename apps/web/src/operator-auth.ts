const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const CREATION_PREFIX = "/creation/";
const OPERATOR_HEADER = "X-Proto-Operator-Token";

let operatorToken: string | null = null;
const nativeFetch = window.fetch.bind(window);

function resolveRequest(input: RequestInfo | URL, init?: RequestInit): { method: string; url: URL } {
  const method = (
    init?.method
    ?? (input instanceof Request ? input.method : "GET")
  ).toUpperCase();
  const rawUrl = input instanceof Request ? input.url : input.toString();
  return { method, url: new URL(rawUrl, window.location.origin) };
}

function acquireOperatorToken(): string | null {
  if (operatorToken) return operatorToken;
  const supplied = window.prompt("PROTO operator token");
  const normalized = supplied?.trim() ?? "";
  if (!normalized) return null;
  operatorToken = normalized;
  return operatorToken;
}

window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const { method, url } = resolveRequest(input, init);
  const sameOrigin = url.origin === window.location.origin;
  const operatorMutation = (
    sameOrigin
    && MUTATING_METHODS.has(method)
    && !url.pathname.startsWith(CREATION_PREFIX)
  );

  if (!operatorMutation) {
    return nativeFetch(input, init);
  }

  const token = acquireOperatorToken();
  if (!token) {
    return new Response(
      JSON.stringify({ detail: "Operator authentication required" }),
      {
        status: 401,
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
  headers.set(OPERATOR_HEADER, token);

  const response = await nativeFetch(input, { ...init, headers });
  if (response.status === 401) {
    operatorToken = null;
  }
  return response;
};
