# AWS Deployment Guide — Onsumm AP Automation

**Architecture:**
- **Frontend** → AWS Amplify (free CDN + auto SSL + git push deploy)
- **Backend** → AWS EC2 Ubuntu 22.04 (FastAPI + Nginx + SSL)
- **Database** → Neon PostgreSQL (already hosted, no change)

---

## Prerequisites

- AWS account with EC2 + Amplify access
- A domain name (e.g. `onsumm.ai`)
  - Suggested subdomains: `app.onsumm.ai` (frontend), `api.onsumm.ai` (backend)
- GitHub repo access
- Your `.env` values ready (DB credentials, OpenAI key, etc.)

---

## PART 1 — EC2 Backend

### Step 1: Launch EC2 Instance

1. Go to **AWS Console → EC2 → Launch Instance**
2. Settings:
   - **Name**: `onsumm-backend`
   - **AMI**: Ubuntu Server 22.04 LTS (free tier eligible)
   - **Instance type**: `t3.micro` (8–10 USD/month) or `t3.small` for more RAM
   - **Key pair**: Create new → download `.pem` file → save it safely
   - **Security Group** — Add these inbound rules:

     | Type  | Port | Source    |
     |-------|------|-----------|
     | SSH   | 22   | My IP     |
     | HTTP  | 80   | 0.0.0.0/0 |
     | HTTPS | 443  | 0.0.0.0/0 |

   - **Storage**: 20 GB gp3 (enough for PDF uploads)
3. Click **Launch Instance**
4. Note the **Public IPv4 address**

---

### Step 2: SSH into EC2

```bash
# On your local machine
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

---

### Step 3: Run the Setup Script

```bash
# On EC2 — copy and paste this entire block
curl -O https://raw.githubusercontent.com/onsumm/PILOT-TESTING/subhash/docs/aws-deploy/01-ec2-setup.sh
bash 01-ec2-setup.sh
```

Or manually copy `docs/aws-deploy/01-ec2-setup.sh` to the server and run it.

---

### Step 4: Fill in the .env file

```bash
nano /home/ubuntu/app/backend_ocr/.env
```

Fill in every value:

```env
DB_HOST=your-neon-host.neon.tech
DB_PORT=5432
DB_NAME=neondb
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_SSL=require

TENANT_ID=your_tenant_id
TENANT_NAME=Your Company Name
TENANT_GSTN=your_gstn

OPENAI_API_KEY=sk-...

SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_gmail_app_password
SMTP_ADMIN_EMAIL=admin@onsumm.ai

APPROVAL_SECRET=generate-a-long-random-string-here

POPPLER_PATH=/usr/bin
```

Then restart the API:
```bash
sudo systemctl restart onsumm-api
sudo systemctl status onsumm-api   # should show: active (running)
```

---

### Step 5: Point your domain to EC2

In your domain registrar / Route 53:
```
A record:  api.onsumm.ai  →  YOUR_EC2_PUBLIC_IP
```

Wait 5–10 minutes for DNS to propagate, then test:
```bash
curl http://api.onsumm.ai/health
# should return: {"status":"ok"}
```

---

### Step 6: Set up Nginx config with your domain

```bash
sudo nano /etc/nginx/sites-available/onsumm-api
# Change:  server_name YOUR_DOMAIN;
# To:      server_name api.onsumm.ai;

sudo nginx -t
sudo systemctl reload nginx
```

---

### Step 7: Enable HTTPS with SSL (Certbot)

```bash
sudo certbot --nginx -d api.onsumm.ai
# Follow prompts — enter email, agree to TOS
# Choose option 2: Redirect HTTP to HTTPS
```

Test HTTPS:
```bash
curl https://api.onsumm.ai/health
```

✅ **Backend is live at `https://api.onsumm.ai`**

---

## PART 2 — AWS Amplify Frontend

### Step 1: Update your API URL

In `frontend/.env.production`, set:
```
VITE_API_URL=https://api.onsumm.ai
```

Commit and push:
```bash
git add frontend/.env.production frontend/amplify.yml
git commit -m "chore: configure AWS deployment"
git push origin subhash
```

---

### Step 2: Connect Amplify to GitHub

1. Go to **AWS Console → AWS Amplify → New App → Host web app**
2. Choose **GitHub** → Authorize → Select `onsumm/PILOT-TESTING` repo
3. **Branch**: `subhash`
4. **App settings**:
   - Root directory: `frontend`
   - Build command: `npm run build`  (auto-detected from `amplify.yml`)
   - Output directory: `dist`
5. **Environment Variables** (add these in Amplify console too):
   - `VITE_API_URL` = `https://api.onsumm.ai`
6. Click **Save and deploy**

Amplify will build and deploy automatically (~3–5 minutes).

---

### Step 3: Add custom domain in Amplify

1. Amplify Console → **Domain management → Add domain**
2. Enter `onsumm.ai`
3. Add subdomains:
   - `app.onsumm.ai` → Amplify app (frontend)
4. Amplify will give you **CNAME records** to add to your DNS
5. SSL is provisioned automatically (free)

✅ **Frontend is live at `https://app.onsumm.ai`**

---

## PART 3 — Future Deployments

### Deploy backend changes:

```bash
# SSH into EC2
ssh -i your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
bash /home/ubuntu/app/docs/aws-deploy/02-deploy-update.sh
```

### Deploy frontend changes:

Just **push to the `subhash` branch** — Amplify auto-detects and rebuilds.

```bash
git push origin subhash   # Amplify deploys automatically
```

---

## Useful Commands

```bash
# Check backend status
sudo systemctl status onsumm-api

# View live logs
sudo journalctl -u onsumm-api -f

# Restart backend
sudo systemctl restart onsumm-api

# Check Nginx
sudo systemctl status nginx
sudo nginx -t

# Renew SSL (auto-renewed by certbot, but manual if needed)
sudo certbot renew
```

---

## Cost Estimate

| Service | Cost |
|---------|------|
| EC2 t3.micro | ~$8–10/month |
| EC2 EBS 20GB gp3 | ~$1.6/month |
| Amplify (frontend) | Free tier (first 1000 build mins/month free) |
| Amplify CDN | ~$0.15/GB served |
| Route 53 (optional) | $0.50/hosted zone + $0.40/1M queries |
| **Total** | **~$10–15/month** |

---

## Troubleshooting

**API returns 502 Bad Gateway:**
```bash
sudo systemctl status onsumm-api
sudo journalctl -u onsumm-api -n 50
```

**PDF uploads fail:**
```bash
# Check Poppler is installed
which pdftoppm   # should return /usr/bin/pdftoppm
# Check upload directory permissions
ls -la /home/ubuntu/app/backend_ocr/stored_uploads/
```

**Amplify build fails:**
- Check build logs in Amplify Console
- Make sure `VITE_API_URL` env var is set in Amplify Console → Environment Variables

**CORS errors in browser:**
- The backend needs to allow your Amplify domain
- Add it to the CORS origins in `main.py` (see below)
