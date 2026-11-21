# Smartbox sul Raspberry Pi 5 (registro D-29, D-30, D-36 … D-43)

Installazione su un Raspberry Pi 5 con webcam USB e un monitor HDMI, senza buzzer: l'allarme compare sullo schermo.


> **Procedura guidata (D-40).** Nel progetto completo dell'autore i passi di questa pagina si possono seguire anche
> con una procedura guidata dal Mac (`tools/configura.py`), che non fa parte di questo repository perché gestisce
> insieme dispositivo, backend e dashboard. Qui sono descritti i passi manuali, sufficienti per l'installazione.

## 1. Scheda e primo avvio
Con Raspberry Pi Imager:
- **Sistema:** Raspberry Pi OS Lite (64-bit), oppure quello con desktop se Lite non c'è.
- **Personalizzazione:** hostname `smartbox`, utente `smartbox`, fuso Europe/Rome, paese Wi-Fi IT, SSH solo con chiave pubblica.

La prima volta conviene collegarlo al router con un cavo di rete.

## 2. Pacchetti di sistema e uv (servono sudo e la password dell'utente)
```bash
sudo apt update && sudo apt install -y git libgl1 libglib2.0-0
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 3. Codice
Sul Raspberry serve solo questo repository (il software di bordo). Non contiene dati delle prove (D-01, D-23).
```bash
git clone https://github.com/UniSalento-IDALab-corsoPerfez-25-26/perfez-2526-smartbox-edge-Congedo.git ~/perfez-2526-smartbox-edge-Congedo
```
Se il repository è privato, il Raspberry lo legge con una **chiave deploy in sola lettura**, creata sul Raspberry e aggiunta in GitHub (Settings → Deploy keys):
```bash
ssh-keygen -t ed25519 -f ~/.ssh/smartbox_deploy -N "" -C "smartbox-pi-deploy"
printf 'Host github-smartbox\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/smartbox_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github-smartbox:UniSalento-IDALab-corsoPerfez-25-26/perfez-2526-smartbox-edge-Congedo.git ~/perfez-2526-smartbox-edge-Congedo
```

## 4. Ambiente Python
```bash
cd ~/perfez-2526-smartbox-edge-Congedo && bash deploy/raspberry/setup_pi.sh
```
Crea `.venv` con Python 3.12, mediapipe 0.10.14 e OpenCV headless, poi esegue i test.

## 5. Avvio automatico con allarme a schermo
```bash
sudo cp ~/perfez-2526-smartbox-edge-Congedo/deploy/raspberry/smartbox-schermo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smartbox-schermo
```

## Uso
| Cosa | Comando |
|---|---|
| Stato del servizio | `systemctl status smartbox-schermo` |
| Eventi in diretta | `journalctl -u smartbox-schermo -f` |
| Aggiornare il codice | `cd ~/perfez-2526-smartbox-edge-Congedo && git pull && sudo systemctl restart smartbox-schermo` |
| Fermare | `sudo systemctl stop smartbox-schermo` |
| Disattivare l'avvio automatico | `sudo systemctl disable --now smartbox-schermo` |

- **Monitor:** verde NORMALE, giallo ATTENZIONE, rosso lampeggiante PERICOLO, con il motivo dell'allarme. Senza webcam compare "TELECAMERA NON TROVATA" e il programma riprova ogni 2 s.
- **Eventi:** sono in `~/smartbox-dati/eventi.csv`, fuori dal repository, e se l'invio è attivo vanno anche al server (§7). Non vengono salvate immagini.
- **Anteprima sul Mac o sul PC:** `ssh -L 8090:127.0.0.1:8090 smartbox@<indirizzo>`, poi http://localhost:8090. Ascolta solo su 127.0.0.1.

## 6. Taratura per soggetto (D-36)
Le soglie "occhio chiuso" standard (0,60 / 0,25) sono state misurate sul soggetto 1 e non valgono per tutti (D-23). La taratura le misura sul guidatore che sta davanti alla camera e le salva in `~/smartbox-dati/taratura.yaml`, che resta valido anche dopo un riavvio.

1. Il guidatore si siede in posizione di guida, davanti alla webcam e con il monitor visibile.
2. Si avvia la taratura in uno di questi modi:
   - dal Mac: `ssh smartbox@<indirizzo> touch ~/smartbox-dati/calibra`
   - dall'anteprima (http://localhost:8090): pulsante **Calibra**.
3. Il monitor guida la taratura:
   - **TARATURA** (2 s): ci si prepara;
   - **OCCHI APERTI** (5 s): si guarda la camera senza muoversi;
   - **CHIUDI GLI OCCHI**: si chiudono gli occhi e si conta lentamente fino a 5, poi si riaprono.
4. L'esito compare sul monitor e nel registro (`journalctl -u smartbox-schermo`). Se la taratura non riesce (poca luce oppure occhi aperti e chiusi non distinguibili) restano le soglie precedenti e si può ripetere.

Mentre è in corso la taratura gli allarmi sono sospesi, perché gli occhi chiusi sono voluti. Sulla barra in basso si legge "tarato AAAA-MM-GG" oppure "soglie standard". Quando cambia il guidatore la taratura va rifatta. Per tornare alle soglie standard: `rm ~/smartbox-dati/taratura.yaml && sudo systemctl restart smartbox-schermo`.

## 7. Invio degli eventi al server (D-36)
Ogni cambio di livello (ora, livello, motivo, PERCLOS, EAR) e ogni 10 s un messaggio di stato vengono inviati al backend. Non si inviano immagini. Gli eventi passano da una coda (`~/smartbox-dati/outbox.sqlite`): se il server non risponde restano lì e partono appena torna raggiungibile. L'allarme in cabina non dipende mai dalla rete.

Sul Raspberry, due file in `~/smartbox-dati`, fuori dal repository:
```yaml
# locale.yaml  – impostazioni di questo dispositivo
forward:
  vehicle_id: veicolo-01
  http:
    enabled: true
    url: http://localhost:8000/api/v1
```
```yaml
# secrets.yaml – chmod 600; lo stesso token va nel devices.yaml del server
http:
  token: <token lungo e casuale>
```
Poi `sudo systemctl restart smartbox-schermo`.

**Server di prova sul Mac.** Il backend gira sul Mac, solo su 127.0.0.1. Il token del veicolo sta in un `devices.yaml` fuori dal repository:
```bash
cd ~/perfez-2526-smartbox-backend-Congedo      # repository del backend
SMARTBOX_DEVICES=~/smartbox-server/devices.yaml SMARTBOX_DB=~/smartbox-server/smartbox.sqlite \
  <python con fastapi>/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
Il Raspberry lo raggiunge attraverso un tunnel SSH aperto dal Mac. Lo stesso comando apre anche l'anteprima:
```bash
ssh -i ~/.ssh/smartbox_pi -N -L 8090:127.0.0.1:8090 -R 8000:127.0.0.1:8000 smartbox@<indirizzo>
```
La dashboard è su http://localhost:8000. Quando il tunnel è chiuso gli eventi restano in coda sul Raspberry (la barra in basso mostra "coda N") e arrivano alla riapertura. In seguito, con un server vero (per esempio un VPS), cambia solo `url` in `locale.yaml`.

## 8. Posizione e velocità dallo smartphone (D-37)
Il telefono Android fa da hotspot e il Raspberry vi si collega. L'app **Traccar Client** (open source, Play Store) invia ogni pochi secondi posizione e velocità al Raspberry, anche a schermo spento. La velocità compare in alto sul monitor ("50 km/h" oppure "GPS assente") e nei messaggi di stato. Le posizioni restano sul Raspberry in `~/smartbox-dati/posizioni.csv`. **Oggi non cambiano l'allarme.**

1. **Hotspot del telefono:** nome senza spazi, password, banda 2,4 GHz.
2. **Raspberry sull'hotspot** (comando dell'autore, chiede la password del Wi-Fi):
   ```bash
   sudo nmcli --ask dev wifi connect "NOME_HOTSPOT"
   ```
   Poi `ip -4 addr show wlan0` per leggere l'indirizzo del Raspberry nella rete del telefono.
3. **Sul Raspberry**, in `~/smartbox-dati/locale.yaml`:
   ```yaml
   gps:
     enabled: true
     port: 5055
   ```
   e in `~/smartbox-dati/secrets.yaml` (lo stesso valore va nell'app come "Identificativo dispositivo"):
   ```yaml
   gps:
     token: <identificativo lungo e casuale>
   ```
   Poi `sudo systemctl restart smartbox-schermo`.
4. **Traccar Client:** URL del server `http://<indirizzo del Raspberry>:5055`. Attenzione: l'URL predefinito è il server pubblico di prova `demo.traccar.org` e va sostituito **prima** di accendere il servizio. Identificativo come sopra (controllare carattere per carattere), intervallo 5 s, distanza 0, precisione alta. Dopo ogni modifica si spegne e si riaccende il servizio nell'app.
5. **Permessi su Android (Samsung, One UI):** Impostazioni → Applicazioni → Traccar Client → Autorizzazioni → Posizione → "Consenti sempre" e "Usa posizione precisa"; Notifiche e Attività fisica → Consenti; Batteria → "Senza restrizioni"; Assistenza dispositivo → Batteria → Limiti utilizzo in background → Traccar tra le "App mai in sospensione".

A telefono fermo Traccar smette di inviare per risparmiare batteria; riprende appena ci si muove.

Sicurezza: il ricevitore accetta solo l'identificativo giusto e solo richieste dalla rete dell'hotspot (`wlan0`); dalla rete di casa via cavo risponde "vietato". La posizione di un lavoratore è un dato tutelato (art. 4 dello Statuto dei lavoratori, GDPR): per ora si usa solo su sé stessi.

## 9. Allarmi sul telefono (D-39)
Il Raspberry ospita un piccolo server di notifiche, **ntfy**. L'app ntfy sul telefono, collegato con l'hotspot, riceve ATTENZIONE e PERICOLO con suono e vibrazione, tutto in locale e senza internet.

1. **Installazione sul Raspberry**, una volta sola dall'autore (chiede la password del Raspberry e quella da scegliere per il telefono):
   ```bash
   sudo bash ~/perfez-2526-smartbox-edge-Congedo/deploy/raspberry/setup_ntfy.sh
   ```
2. **Accensione nella Smartbox**, in `~/smartbox-dati/locale.yaml`:
   ```yaml
   outputs:
     phone:
       enabled: true
   ```
   poi `sudo systemctl restart smartbox-schermo`.
3. **App ntfy sul telefono** (Play Store, Philipp C. Heckel):
   - ⋮ → Impostazioni → Gestisci utenti → Aggiungi: server `http://<indirizzo del Raspberry>:2586`, utente `telefono`, password scelta al punto 1;
   - **+** → topic `smartbox` (**tutto minuscolo**: la tastiera mette la maiuscola) → "Usa un altro server", stesso indirizzo;
   - Samsung: notifiche consentite, Batteria "Senza restrizioni", ntfy tra le "App mai in sospensione"; per il PERICOLO, nella categoria "Priorità massima" attivare "Ignora Non disturbare".

| Evento | Notifica |
|---|---|
| ATTENZIONE | priorità alta, suono e vibrazione |
| PERICOLO | urgente; promemoria ogni 30 s finché dura |
| ritorno a NORMALE | discreta, senza suono |

Il messaggio contiene solo livello, motivo e ora. Senza account ntfy risponde "vietato" (403), anche dalla rete di casa.

## 10. Pagina del Raspberry dal telefono (D-41, D-43)
La pagina del Raspberry (video, dati in diretta, Calibra, Protocollo 60 s, prova sbadigli, soglie, prove salvate) si apre:
- dal **Mac/PC** con il collegamento SSH: `http://localhost:8090`, senza password;
- dal **telefono** collegato all'hotspot: `http://<indirizzo del Raspberry nell'hotspot>:8090`, **con password**.

**Android recente:** se il browser del telefono dà `ERR_TOO_MANY_RETRIES` o «Impossibile raggiungere il sito», concedi al browser il permesso **«Dispositivi nelle vicinanze»** (o «Rete locale»): Impostazioni → Applicazioni → Chrome → Autorizzazioni; poi chiudi e riapri Chrome. Senza quel permesso Android blocca i collegamenti verso la rete dell'hotspot.

Si attiva dalla procedura guidata, passo «Pagina del Raspberry sul telefono»: scegli la password (sul Raspberry va solo la sua impronta cifrata), poi «Attiva la pagina sul telefono». Da ogni altra rete la pagina è rifiutata. Dopo 5 password sbagliate l'accesso si blocca per 5 minuti. Il collegamento telefono–Raspberry non è cifrato ma resta dentro l'hotspot.

## 11. Problemi già incontrati e soluzioni
| Sintomo | Causa | Soluzione | Registro |
|---|---|---|---|
| **Dal telefono la pagina non si apre**: Chrome o Samsung Internet dicono «Impossibile raggiungere il sito» / `ERR_TOO_MANY_RETRIES`, mentre ntfy e Traccar funzionano | Android recente blocca i collegamenti dei browser verso la rete locale (l'hotspot) senza un permesso apposito; sul Raspberry non arriva nessun collegamento | Impostazioni → Applicazioni → **Chrome** → Autorizzazioni → **«Dispositivi nelle vicinanze»** (o «Rete locale») → Consenti; chiudere e riaprire Chrome | D-43 |
| L'indirizzo `http://<indirizzo-hotspot-del-Raspberry>:8090` non si apre **dal Mac** | Il Mac è sulla rete di casa, non su quella dell'hotspot | È normale: quell'indirizzo si apre solo dal telefono; dal Mac si usa `localhost:8090` con il collegamento SSH | D-43 |
| La pagina del telefono non chiede la password e non risponde | Password non salvata (con due pulsanti separati le caselle si svuotavano) | Procedura guidata → «Imposta la password e attiva» (un solo pulsante), poi «Verifica» | D-43 |
| La pagina dal Mac è «bloccata» o ferma sull'ultima immagine | Il collegamento SSH si è chiuso insieme alla procedura guidata, oppure è aperto il file `pagina.html` dal disco | Procedura guidata → Pagina iniziale → «Raspberry dal vivo» → Apri; l'indirizzo giusto è `localhost:8090` | D-41 |
| Calibra «non riuscita: occhi aperti e chiusi non distinguibili» | Volto perso o occhi non ben chiusi durante la fase a occhi chiusi; occhiali | Viso intero e centrato, webcam all'altezza degli occhi a 50–70 cm; occhi ben chiusi contando fino a 5 | D-36, D-41 |
| «Salva» delle soglie riscriveva la taratura | Il riquadro mostrava ancora le soglie vecchie | Corretto: si salvano solo i campi cambiati; dopo una taratura il riquadro si aggiorna | D-41 |
| Il capo chinato non fa scattare «chinamento» | Webcam sul tavolo (inquadra dal basso) e volto perso chinandosi | Taratura della posizione del capo; chinamento anche con volto perso; soglia per ora 15° | D-41, D-42 |
| Circa 6 immagini al secondo invece di 12 | Buffer singolo della webcam USB su Linux | Corretto in `sources.py` | D-38 |
| Meno di 10 immagini al secondo | Poca luce: la webcam generica allunga l'esposizione | Più luce o camera migliore (infrarossi per la notte) | D-38 |
| Traccar non invia nulla | URL predefinito `demo.traccar.org`; permessi di Android; identificativo con un carattere sbagliato | URL del Raspberry; «Consenti sempre» e permessi Samsung; identificativo carattere per carattere | D-37 |
| ntfy: «accesso negato» sul telefono | Topic scritto con la maiuscola (`Smartbox`) | Topic `smartbox` tutto minuscolo | D-39 |
| Il comando con `sudo` non accetta la password | Lanciato nel terminale del **Mac** invece che sul Raspberry | Usare «Apri nel Terminale» della procedura guidata: il comando va già al Raspberry | D-33, D-40 |

## Come è fatto il programma (D-38)
`schermo.py` non contiene logica propria: avvia il ciclo unico `python -m smartbox.main --screen --preview 8090 --data-dir ~/smartbox-dati`. Ogni funzione è un modulo indipendente di `smartbox/`: monitor (`display.py`), anteprima (`preview.py`), taratura (`calibration.py`), invio (`publisher.py`), GPS (`gps.py`), buzzer/LED (`alerts.py`), telefono (`phone.py`). Le funzioni si accendono dalla riga di comando o dalla configurazione: `config.yaml` del repository, poi `locale.yaml`, `taratura.yaml` e `secrets.yaml` in `~/smartbox-dati`.

`smartbox.service` (in `deploy/`) avvia lo stesso programma senza monitor, con il codice in `/opt/smartbox`. I due servizi si usano in alternativa, perché entrambi aprono la webcam. Con buzzer e LED collegati basta mettere `outputs.gpio_enabled: true` in `locale.yaml`.
