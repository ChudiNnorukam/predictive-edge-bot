# Oracle Cloud VPS Setup Guide - Lessons Learned

## What Went Wrong (First Attempt - Jan 30, 2026)

### Problem: SSH Key Format Issue
- **Symptom**: `Load key "polymarket_key": invalid format` error
- **Root Cause**: The SSH key file was likely corrupted during creation/saving
  - Possibly copy-pasted from Oracle Cloud console with formatting issues
  - May have had invisible characters, wrong line endings, or encoding problems
- **Result**: Unable to SSH into instance from Mac or Cloud Shell

### Key Lessons
1. **NEVER copy-paste SSH keys from web interfaces** - always download directly or generate locally
2. **Generate SSH keys on your local machine FIRST**, then provide the public key to Oracle Cloud
3. **Always test the key immediately** after generating: `ssh-keygen -y -f keyfile` should output the public key
4. **Set correct permissions**: `chmod 600` for private key, `chmod 644` for public key

---

## Correct Setup Process

### Step 1: Generate SSH Key Locally (BEFORE creating instance)
```bash
# Generate a new RSA key pair with no passphrase
ssh-keygen -t rsa -b 4096 -f ~/.ssh/oracle_polymarket -N ""

# Verify the key works
ssh-keygen -y -f ~/.ssh/oracle_polymarket

# Set correct permissions
chmod 600 ~/.ssh/oracle_polymarket
chmod 644 ~/.ssh/oracle_polymarket.pub

# Copy the public key (you'll paste this in Oracle Cloud)
cat ~/.ssh/oracle_polymarket.pub
```

### Step 2: Create Oracle Cloud Instance
1. Go to Oracle Cloud Console → Compute → Instances → Create Instance
2. Name: `polymarket-bot-1`
3. Image: Oracle Linux 9 (or Ubuntu 22.04)
4. Shape: VM.Standard.E2.1.Micro (Always Free)
5. **SSH Key**: Paste the PUBLIC key from Step 1
6. Create the instance

### Step 3: Connect via SSH
```bash
# Connect using the private key
ssh -i ~/.ssh/oracle_polymarket opc@<PUBLIC_IP>

# For Ubuntu images, use:
ssh -i ~/.ssh/oracle_polymarket ubuntu@<PUBLIC_IP>
```

### Step 4: If Connection Fails
1. Check key permissions: `ls -la ~/.ssh/oracle_polymarket`
2. Verify key format: `ssh-keygen -y -f ~/.ssh/oracle_polymarket`
3. Test with verbose mode: `ssh -v -i ~/.ssh/oracle_polymarket opc@<IP>`
4. Ensure Security List allows SSH (port 22) - Oracle Cloud creates this by default

---

## Oracle Cloud Free Tier Specs

| Resource | Limit |
|----------|-------|
| Compute | 2 VM.Standard.E2.1.Micro instances |
| Storage | 200 GB total block volume |
| Bandwidth | 10 TB/month outbound |
| Region | Amsterdam (eu-amsterdam-1) |

---

## Quick Reference

### SSH Command
```bash
ssh -i ~/.ssh/oracle_polymarket opc@84.235.168.150
```

### Key Locations
- Private key: `~/.ssh/oracle_polymarket`
- Public key: `~/.ssh/oracle_polymarket.pub`

### Default Users
- Oracle Linux: `opc`
- Ubuntu: `ubuntu`

---

*Last updated: January 30, 2026*
