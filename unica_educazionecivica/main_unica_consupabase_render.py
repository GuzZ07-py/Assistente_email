import os
import base64
import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import requests
import urllib.parse
import json
import re
import psycopg2
from dotenv import load_dotenv
import time
# --- CONFIGURAZIONE ---
API_KEY_GEMINI = os.getenv("API_GEMINI_KEY")


GOOGLE_MAPS_API_KEY= os.getenv("GOOGLE_MAPS_API_KEY")
SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
    
]

genai.configure(api_key=API_KEY_GEMINI)

load_dotenv()

# Fetch variables
DATABASE_URL = os.getenv("DATABASE_URL")



def esegui_query(query: str):
    #sicurezza
    query_lower = query.lower()

    if not query_lower.strip().startswith("select"):
        return "Solo query SELECT consentite."
    
    #codice per query
    try:
        conn=psycopg2.connect(DATABASE_URL)
        print("connessione riuscita")
        cursor=conn.cursor()
        
        cursor.execute(query)
        result=cursor.fetchall()

        conn.close()

        return str(result)
    
    except Exception as e:
        return f"Errore SQL:{e}"
#funzione per la pulizia
def pulisci_json(text):
    text=text.strip()
    text = re.sub(r"^```json", "", text)
    text = re.sub(r"^```", "", text)
    text = re.sub(r"```$", "", text)
    
    return text.strip()

def stima_consegna(orig, dest):
    try:
        orig = urllib.parse.quote(orig)
        dest = urllib.parse.quote(dest)

        url = "https://maps.googleapis.com/maps/api/distancematrix/json"

        params = {
            "origins": orig,
            "destinations": dest,
            "departure_time": "now",
            "key": GOOGLE_MAPS_API_KEY
        }

        res = requests.get(url, params=params).json()

        elemento = res['rows'][0]['elements'][0]

        distanza = elemento['distance']['text']
        durata = elemento.get('duration_in_traffic', elemento['duration'])['text']

        return distanza, durata

    except Exception as e:
        return None, None

def inserisci_ordine(json_data: str):
    try:
        data = json.loads(json_data)

        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()

        # 1. CLIENTE
        cursor.execute("SELECT id FROM clienti WHERE nome=%s AND email=%s",(data["cliente"]["nome"],data["cliente"]["email"],))
        result = cursor.fetchone()
        if result:
            cliente_id=result[0]
        else:
            cursor.execute("""
            INSERT INTO clienti (nome, email, indirizzo)
            VALUES (%s, %s, %s)
            RETURNING id""", 
            (
            data["cliente"]["nome"],
            data["cliente"]["email"],
            data["cliente"]["indirizzo"]
            ))
            cliente_id = cursor.fetchone()[0]
        print("ok cliente")
        # 2. CARTA
            #controllo se gia presente
        cursor.execute("SELECT id FROM carte WHERE last4=%s AND token=%s",(data["carta"]["last4"],data["carta"]["token"],))
        result = cursor.fetchone()

        if result:
            carta_id=result[0]
        else:
            cursor.execute("""
            INSERT INTO carte (cliente_id, last4, circuito, scadenza, token)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id""", 
            (
            cliente_id,
            data["carta"]["last4"],
            data["carta"]["circuito"],
            data["carta"]["scadenza"],
            data["carta"]["token"]
            ))
            carta_id = cursor.fetchone()[0]
        print("ok carte")


        # 3. CORRIERE (lookup o insert)
        cursor.execute("SELECT id FROM corrieri WHERE nome=%s", (data["corriere"]["nome"],))
        result = cursor.fetchone()

        if result:
            corriere_id = result[0]
        else:
            cursor.execute("INSERT INTO corrieri (nome) VALUES (%s) RETURNING id", (data["corriere"]["nome"],))
            corriere_id = cursor.fetchone()[0]
        print("ok corrieri")
        # 4. ORDINE

         #id_ordine=data["ordine"].get("id") or data["ordine"].get("id_ordine")

        cursor.execute("""
            INSERT INTO ordini 
            (data_spedizione, data_arrivo, tracking, stato, origine, cliente_id, corriere_id, carta_id)
            VALUES ( %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (
            
            data["ordine"]["data_spedizione"],
            data["ordine"]["data_arrivo"],
            data["ordine"]["tracking"],
            data["ordine"]["stato"],
            data["ordine"]["origine"],
            cliente_id,
            corriere_id,
            carta_id
        ))

        ordine_id=cursor.fetchone()[0]
        print("ok ordini")
        #5 PRODOTTO 
        cursor.execute("SELECT id FROM prodotti WHERE nome=%s", (data["prodotti"]["nome"],)
        )
        result= cursor.fetchone()
        if result:
            prodotto_id=result[0] # i prodotti sono questi
            print("ok prodotti")
        else:
            raise Exception(f"Prodotto {data["prodotti"]["nome"]} non trovato")
        
        #6 ordini_prodotti
        
        cursor.execute("""INSERT INTO ordini_prodotti (ordine_id,prodotto_id,quantita) VALUES (%s,%s,%s)""",
                       (
                           ordine_id,
                           prodotto_id,
                           data["ordini_prodotti"]["quantita"]
                       ))

       

        print("ok ordini_prodotti")
        conn.commit()
        print("commit OK")
        conn.close()

        return "Ordine inserito correttamente"

    except Exception as e:
        return f"Errore inserimento: {e}"

# 2. AUTENTICAZIONE GMAIL

def get_credentials():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials7.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds
    
# 3. LOGICA PRINCIPALE
def process_last_email():
    
    
    creds=get_credentials()
    service = build('gmail', 'v1', credentials=creds)
    



    # Cerca l'ultima email non letta
    results = service.users().messages().list(userId='me', q='is:unread', maxResults=1).execute()
    messages = results.get('messages', [])

    if not messages:
        print("Nessuna nuova email non letta.")
        return

    msg = service.users().messages().get(userId='me', id=messages[0]['id']).execute()
    
    # Estrazione testo (semplificata)
    payload = msg['payload']
    parts = payload.get('parts')
    body = ""
    if parts:
        data = parts[0]['body'].get('data')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8')

    print(f"--- Email Ricevuta ---\n{body}\n----------------------")

    # Inizializza Gemini con la funzione di tracciamento
    model = genai.GenerativeModel(
        model_name='gemini-2.5-pro', # La tua versione
        tools=[stima_consegna,esegui_query]
    )
    
    chat = model.start_chat(enable_automatic_function_calling=True)
    response = chat.send_message(f"""
    Se l'email è una conferma ordine:
    - restituisci SOLO JSON valido
    - senza testo
    - senza saluti
    - senza markdown
    - senza ```json
    - usa esattamente questi campi:

    cliente: nome, email, indirizzo  
    carta: last4, circuito, scadenza, token  
    corriere: nome  
    ordine: data_spedizione, data_arrivo, tracking, stato, origine  
    ordini_prodotti : ordini_id,prodotto_id,quantita
    prodotti: id,nome,descrizione,quantita_in_magazzino,prezzo



    Se l'email riguarda un ordine rispondi con la funzione:
    -esegui_query
                            
    Rispondi usando gli ordini contenuti nel database
    
    Il database è strutturato in questo modo: 

    TABELLE:

    clienti(id, nome, email, indirizzo)
    corrieri(id, nome)
    carte(id, cliente_id, last4, circuito, scadenza, token)
    ordini(id, data_spedizione, data_arrivo, tracking, stato, origine, cliente_id, corriere_id, carta_id)
    prodotti(id,nome,descrizione,quantita_in_magazzino,prezzo)
    ordini_prodotti(ordine_id,prodotto_id,quantita)
                                 
    RELAZIONI:
                                 
    - ordini.cliente_id → clienti.id
    - ordini.corriere_id → corrieri.id
    - ordini.carta_id → carte.id
    - ordini_prodotti.ordine_id -> ordini.id
    - ordini_prodotti.prodotto_id -> prodotti.id

    ESEMPI:

    - " Chi è il corriere del mio ordine con ID 6" -> SELECT nome FROM corrieri c JOIN ordini o ON c.corriere_id = o.corriere_id WHERE o.id='ORD006'
    
    - "Tempo medio di spedizione del corriere Bartolini" -> SELECT AVG(data_arrivo - data_spedizione) FROM ordini JOIN corrieri ON ordini.corriere_id = corrieri.id WHERE corrieri.nome = 'Bartolini';
    
    - "Quale è il prezzo del mio prodotto con id 18" -> SELECT prezzo FROM prodotti p JOIN ordini_prodotto op ON op.prodotto_id=p.id AND op.ordine_id= 18

    - "Quale è la quantita rimasta nel magazzino del prodotto  Smartphone X10" -> SELECT   quantita_in_magazzino FROM prodotti WHERE nome='Smartphone X10'
    
    Rispondi anche alle richieste nelle altre lingue, usando la lingua utilizzata dal mittente,
    
                                 
    Nel caso che l'email riguardi una domanda su un ordine rispondi:
    -Sempre Ringrazione e Salutando con Salve Gentile Cliente,...
    -Sempre in modo formale
    EMAIL:
    {body}
    """)

    #print(f"\n--- Risposta Suggerita da Gemini ---\n{response.text}")
    


    try:
        print(response.text)
        json_pulito=pulisci_json(response.text)
        json.loads(json_pulito)
        risultato = inserisci_ordine(json_pulito)
        
        print("Funzione chiamata")
        print(risultato)
    except Exception as e:
        print("Errore nel Parsing JSON: ",e)
        





    #PARTE DELLA RISPOSTA AUTOMATICA

    risposta_ai=response.text
    
    headers = msg['payload'].get('headers', [])
    subject = next(h['value'] for h in headers if h['name'] == 'Subject')
    sender = next(h['value'] for h in headers if h['name'] == 'From')
    thread_id = msg['threadId']

    # 2. Prepariamo il messaggio email
    message = MIMEText(risposta_ai)
    message['to'] = sender
    message['subject'] = f"Re: {subject}"
    
    # Codifica in base64 richiesta da Gmail API
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    # 3. Invio effettivo
    try:
        service.users().messages().send(
            userId='me', 
            body={'raw': raw_message, 'threadId': thread_id}
        ).execute()
        print(f"Risposta inviata con successo a: {sender}")
        
        # 4. SEGNA COME LETTA (Importante per non rispondere due volte!)
        service.users().messages().batchModify(
            userId='me', 
            body={'removeLabelIds': ['UNREAD'], 'ids': [messages[0]['id']]}
        ).execute()
        
    except Exception as e:
        print(f"Errore durante l'invio: {e}")
    
    # Opzionale: Segna come letta per non riprocessarla
    # service.users().messages().batchModify(userId='me', body={'removeLabelIds': ['UNREAD'], 'ids': [messages[0]['id']]}).execute()

if __name__ == "__main__":
    while True:
        try:
            process_last_email()
        except Exception as e:
            print("Errore:",e)

        time.sleep(30)