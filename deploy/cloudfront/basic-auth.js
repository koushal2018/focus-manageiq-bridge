// CloudFront Function (viewer-request) — HTTP Basic Auth at the edge.
// Gates the synthetic AnyBank FinOps PoC console so a bank-branded demo is
// never on the open internet unauthenticated. Credentials are demo-only;
// the warehouse is synthetic (DEMO-*). Rotate by republishing this function.
function handler(event) {
    var request = event.request;
    var headers = request.headers;
    // <BASE64_USER_COLON_PASS> = printf 'user:pass' | base64 — kept out of git
    // on purpose; the LIVE published function holds the real value.
    var expected = "Basic <BASE64_USER_COLON_PASS>";
    if (!headers.authorization || headers.authorization.value !== expected) {
        return {
            statusCode: 401,
            statusDescription: "Unauthorized",
            headers: {
                "www-authenticate": { value: 'Basic realm="AnyBank FinOps PoC (synthetic)"' }
            }
        };
    }
    return request;
}
