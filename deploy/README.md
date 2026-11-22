# Backend sulla VM Oracle Cloud (Always Free)

La parte statica del progetto è su **GitHub Pages**; il server gira su una VM Oracle Cloud
*Always Free*, condivisa con gli altri progetti (BoatRadar, DressIt, OpenVitals.io,
QuantTradingSystem). Costo: **0 €/mese** entro i limiti Always Free (Ampere A1: 2 OCPU e
12 GB di RAM in totale, 200 GB di disco, 10 TB/mese di traffico in uscita).

## Una volta sola: crea la VM

1. Registrati su <https://www.oracle.com/cloud/free/> (serve una carta per la verifica;
   finché resti nei limiti Always Free non viene addebitato nulla). Scegli una *home region*
   vicina, per esempio Frankfurt o Milan: non si può cambiare dopo.
2. **Compute → Instances → Create instance**
   - Image: **Ubuntu 24.04** · Shape: **VM.Standard.A1.Flex**, 2 OCPU e 12 GB.
   - Rete: lascia la VCN proposta con IP pubblico.
   - SSH keys: *Generate a key pair* e scarica la **chiave privata**.
   Se compare *Out of capacity*, riprova più tardi o scegli un altro *availability domain*.
3. **Networking → Virtual cloud networks → (la tua VCN) → Security → Security List di default →
   Add Ingress Rules**: sorgente `0.0.0.0/0`, TCP, porte di destinazione `80,443`.
   (Il firewall interno della VM lo apre da solo `deploy/bootstrap.sh`.)
4. Annota l'**IP pubblico** della VM.

Docker, firewall e Caddy (HTTPS automatico con Let's Encrypt) li installa il primo deploy.

## Per ogni repository

In **Settings → Secrets and variables → Actions**:

| Tipo | Nome | Valore |
|---|---|---|
| Variable | `VM_HOST` | IP pubblico della VM, es. `130.61.10.20` |
| Variable | `VM_USER` | facoltativa, di base `ubuntu` |
| Secret | `VM_SSH_KEY` | contenuto della chiave privata scaricata al punto 2 |
| Variable | `API_HOST` | facoltativa: tuo dominio per il server, es. `api.miosito.it` |

Gli altri secret dipendono dal progetto (vedi il README principale).

Poi in **Settings → Pages → Build and deployment → Source** scegli **GitHub Actions**
(se il progetto ha una parte su Pages) e lancia una volta da **Actions** i workflow
*Deploy backend* e *GitHub Pages* (*Run workflow*). Il primissimo deploy installa Docker sulla VM:
fallo per un progetto alla volta, poi gli altri. Dai push successivi su `main` si
aggiornano da soli.

## Indirizzi

Senza un dominio, ogni server riceve un nome gratuito di [sslip.io](https://sslip.io) che punta
all'IP della VM, con certificato HTTPS valido: `<app>.<IP con i trattini>.sslip.io`,
per esempio `boatradar.130-61-10-20.sslip.io`. Con un dominio tuo crea un record DNS `A`
verso l'IP e mettilo in `API_HOST`.

## Comandi utili sulla VM

```bash
ssh -i chiave.key ubuntu@IP
sudo docker ps                                  # container attivi
sudo docker logs -f <app>                       # log di un'app
sudo docker compose -p <app> -f /opt/apps/<app>/deploy/compose.yml restart
```
