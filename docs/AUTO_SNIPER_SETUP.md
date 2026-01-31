# Auto-Sniper Setup Guide

Automatically runs the sniper bot during market hours (Monday-Friday, 9 AM - 11 PM ET).

---

## Step 1: Create the Auto-Start Script

SSH into your VPS:
```bash
ssh -i ~/.ssh/oracle_polymarket root@142.93.143.178
```

Create the script:
```bash
nano /opt/polymarket-bot/auto_sniper.sh
```

Paste this content:
```bash
#!/bin/bash
cd /opt/polymarket-bot
source venv/bin/activate

# Get token ID from scanner
TOKEN=$(python scanner.py --direct --json 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); print(d[0]['yes_token_id'] if d else '')" 2>/dev/null)

if [ -n "$TOKEN" ]; then
    echo "$(date): Found market, starting sniper with token $TOKEN"
    pm2 delete sniper 2>/dev/null
    pm2 start sniper.py --name "sniper" --interpreter ./venv/bin/python -- --token-id $TOKEN
else
    echo "$(date): No markets found"
fi
```

Save: `Ctrl+O` → Enter → `Ctrl+X`

---

## Step 2: Make Script Executable

```bash
chmod +x /opt/polymarket-bot/auto_sniper.sh
```

---

## Step 3: Create Logs Directory

```bash
mkdir -p /opt/polymarket-bot/logs
```

---

## Step 4: Set Up Cron Job

Open crontab:
```bash
crontab -e
```

Add this line (runs every 15 minutes, Mon-Fri, 6 AM - 8 PM PT / 9 AM - 11 PM ET):
```
*/15 6-20 * * 1-5 /opt/polymarket-bot/auto_sniper.sh >> /opt/polymarket-bot/logs/auto.log 2>&1
```

Save and exit (in nano: `Ctrl+O` → Enter → `Ctrl+X`)

---

## Step 5: Verify Cron is Set

```bash
crontab -l
```

Should show:
```
*/15 6-20 * * 1-5 /opt/polymarket-bot/auto_sniper.sh >> /opt/polymarket-bot/logs/auto.log 2>&1
```

---

## How It Works

| Time | What Happens |
|------|--------------|
| Every 15 min (market hours) | Cron triggers `auto_sniper.sh` |
| Script runs scanner | Looks for active 15-min markets |
| If market found | Starts sniper with that token ID |
| If no market | Logs "No markets found", waits for next run |

---

## Monitoring

**Check auto-start logs:**
```bash
tail -f /opt/polymarket-bot/logs/auto.log
```

**Check sniper status:**
```bash
pm2 status
pm2 logs sniper
```

**Check cron is running:**
```bash
grep CRON /var/log/syslog | tail -20
```

---

## Manual Override

**Stop automatic trading:**
```bash
pm2 stop sniper
crontab -e  # Comment out or delete the line
```

**Start manually:**
```bash
cd /opt/polymarket-bot
source venv/bin/activate
python scanner.py --direct
python sniper.py --token-id <TOKEN_ID>
```

---

## Timezone Reference

| Your Time (PT) | Market Time (ET) | Markets Active? |
|----------------|------------------|-----------------|
| 6:00 AM | 9:00 AM | Yes |
| 8:00 PM | 11:00 PM | Yes |
| 8:01 PM | 11:01 PM | No |
| 5:59 AM | 8:59 AM | No |

---

## Troubleshooting

**Bot not starting?**
1. Check if markets exist: `python scanner.py --direct`
2. Check logs: `tail -50 /opt/polymarket-bot/logs/auto.log`
3. Check PM2: `pm2 logs sniper --lines 50`

**Cron not running?**
1. Verify crontab: `crontab -l`
2. Check cron service: `systemctl status cron`
3. Check syslog: `grep CRON /var/log/syslog | tail -20`

**Wrong timezone?**
```bash
timedatectl set-timezone America/New_York
```
Then update cron hours accordingly.
