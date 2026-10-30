# Dev Environment (uat2)

A second backend environment ("dev") running the **`uat2`** branch, alongside prod, on the
**same EC2 instance**. Both are reachable over HTTPS through the existing `api.onsumm.ai`
certificate — dev lives under the `/dev` path, so **no new domain or TLS cert was needed**.

Last updated: 2026-07-05

---

## 1. Topology

```
                       api.onsumm.ai (HTTPS :443, Certbot cert)
                                    │  nginx
                 ┌──────────────────┴───────────────────┐
        location /                             location /dev/
                 │                                       │
        127.0.0.1:8001                          127.0.0.1:8002
        onsumm-api  (PROD)                       onsumm-api-dev (DEV)
        ~/app  ·  branch: subhash               ~/app-dev · branch: uat2
                 │                                       │
                 └──────────── shared prod RDS ─────────┘
```

- **EC2 instance:** `i-02d5904600dd95e1e` · region **ap-south-1 (Mumbai)** · public IP `13.232.207.113`
- **SSH:** `ssh -i ~/Downloads/onsumm-mumbai.pem ec2-user@13.232.207.113`
- **Security group:** `sg-0fffe5face8e42866` (`launch-wizard-1`, ap-south-1)

---

## 2. Backend services

| | PROD | DEV |
|---|---|---|
| systemd unit | `onsumm-api` | `onsumm-api-dev` |
| Code dir | `/home/ec2-user/app` | `/home/ec2-user/app-dev` |
| Branch | `subhash` | `uat2` |
| Port (localhost) | 8001 | 8002 |
| Env file | `/home/ec2-user/app/.env` | `/home/ec2-user/app-dev/.env` |
| Public URL | `https://api.onsumm.ai/` | `https://api.onsumm.ai/dev/` |
| Runtime | `python3.11 -m uvicorn app.main:app --host 0.0.0.0 --port <port>` | same |

Both units: `User=ec2-user`, `Restart=always`, `WantedBy=multi-user.target` (survive reboot).

Health checks:
```bash
curl -o /dev/null -w "%{http_code}\n" https://api.onsumm.ai/docs         # prod  -> 200
curl -o /dev/null -w "%{http_code}\n" https://api.onsumm.ai/dev/openapi.json  # dev -> 200
```

---

## 3. nginx routing

File: `/etc/nginx/conf.d/onsumm-api.conf` (backup saved as `onsumm-api.conf.bak.<ts>`).

The `api.onsumm.ai` 443 server block has two locations:

```nginx
location /dev/ {
    proxy_pass http://127.0.0.1:8002/;   # trailing slash strips the /dev prefix
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 300;
    proxy_send_timeout 300;
    client_max_body_size 50M;
}

location / {
    proxy_pass http://127.0.0.1:8001;    # prod, unchanged
    ...
}
```

Because of the trailing slash on `proxy_pass`, a request to
`https://api.onsumm.ai/dev/api/bills` is forwarded to the dev backend as `/api/bills`.

Apply config changes safely:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## 4. Frontend (Amplify) — deploy uat2 against dev

Point the dev frontend at the dev backend via the API base-URL env var. In
`frontend/src/api/invoices.ts`: `axios.defaults.baseURL = import.meta.env.VITE_API_URL`.

**Amplify app settings for the dev deployment:**
- **Repository branch:** `uat2`
- **Environment variable:** `VITE_API_URL = https://api.onsumm.ai/dev`
- Build: standard Vite build (`npm ci && npm run build`, artifact dir `dist`).

With that, all API calls resolve to `https://api.onsumm.ai/dev/...` over HTTPS — no
mixed-content issue.

> Do **not** point the dev frontend at `http://13.232.207.113:8002` — an HTTPS Amplify
> site cannot call a plain-HTTP endpoint (browser blocks it as mixed content). Always use
> the `https://api.onsumm.ai/dev` path.

---

## 5. Updating / redeploying the dev backend

```bash
ssh -i ~/Downloads/onsumm-mumbai.pem ec2-user@13.232.207.113
cd ~/app-dev
git fetch origin uat2 && git checkout uat2 && git pull            # get latest uat2
# (install deps only if requirements changed)
# python3.11 -m pip install --user -r backend_ocr/requirements.txt
sudo systemctl restart onsumm-api-dev
systemctl is-active onsumm-api-dev
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002/docs  # -> 200
```

> ⚠️ As of this writing the dev box is at `uat2 @ 3adbb18`, which is **behind**
> `origin/uat2 @ 55f813f` (approval-gate whitespace fix + assigned-submitter resubmit).
> Run the update above to pull those in before testing approvals.

Logs: `sudo journalctl -u onsumm-api-dev -n 100 --no-pager`

---

## 6. Shared-database caveats (important)

Dev uses the **same production RDS** and the **same `JWT_SECRET`/`APP_URL`** as prod
(copied `.env`). Consequences:

- **Data is shared.** Any create/approve/send-back/edit in dev writes to the real prod
  data. Treat dev writes as production writes until dev gets its own DB.
- Dev's startup DDL (`CREATE TABLE / ADD COLUMN IF NOT EXISTS`) runs against the shared
  DB — additive/idempotent, but the schema now carries uat2's extra columns/tables.
- Same JWT secret → tokens are valid across both; approval emails use the prod `APP_URL`.

To fully isolate dev later: point `app-dev/.env` `DB_URL` at a separate database and set a
distinct `JWT_SECRET`/`APP_URL`, then `sudo systemctl restart onsumm-api-dev`.

---

## 7. Security group / ports

- Public HTTPS (443) + HTTP (80) are open; everything routes through nginx.
- Raw port **8002 does not need to be public** — access is via `api.onsumm.ai/dev`. If a
  temporary `0.0.0.0/0` rule for 8002 was added to `sg-0fffe5face8e42866` during setup, it
  can be removed.
- A stray 8002 rule was mistakenly added to an unrelated **eu-north-1** SG
  (`sg-0bcfb5b36662953bb`) during setup — delete it; it has no effect here.

---

## 8. Rollback (remove dev)

```bash
# stop + disable dev service
sudo systemctl disable --now onsumm-api-dev
# restore nginx (removes the /dev location)
sudo cp /etc/nginx/conf.d/onsumm-api.conf.bak.<ts> /etc/nginx/conf.d/onsumm-api.conf
sudo nginx -t && sudo systemctl reload nginx
```

Prod (`onsumm-api`, port 8001, `location /`) is untouched by any of the above.
