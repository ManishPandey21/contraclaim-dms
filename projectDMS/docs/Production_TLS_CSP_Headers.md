# Production TLS, HSTS, and CSP Requirements

ContraClaim must be served only through HTTPS in production. The application container may listen on HTTP inside a private network, but an upstream load balancer, ingress controller, CDN, or reverse proxy must terminate TLS and forward only trusted traffic to the application.

## Required TLS Controls

- Terminate TLS with TLS 1.2 or newer; TLS 1.3 is preferred.
- Redirect all plain HTTP requests to HTTPS at the edge.
- Use managed certificates with automated renewal.
- Do not expose the backend container HTTP port directly to the internet.
- Set `AUTH_COOKIE_SECURE=true` in production so the HttpOnly auth cookie is sent only over HTTPS.
- Use `AUTH_COOKIE_SAMESITE=lax` or `strict` unless cross-site embedding is intentionally required.

## Headers Configured In Repository

`config/httpd.conf` now sets:

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy` with `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'self'`, and restricted asset/connect sources.
- `Permissions-Policy` disabling camera, microphone, geolocation, and payment APIs.

HSTS is effective only when delivered over HTTPS. Keep this header enabled at the edge proxy as well if Apache is not the public entrypoint.

## Example Nginx Edge Policy

```nginx
server {
    listen 80;
    server_name app.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name app.example.com;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; connect-src 'self' https: wss:; worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;

    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_pass http://frontend:80;
}
```

## Production Acceptance

- Browser devtools shows HTTPS and the configured security headers on app responses.
- Auth cookie is `HttpOnly`, `Secure`, and has the configured `SameSite` value.
- API and WebSocket connections use HTTPS/WSS from the browser.
- Direct public access to internal backend HTTP ports is blocked.
