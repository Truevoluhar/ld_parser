# LD parser — samostojen JSON API in GUI

Storitev iz skeniranih izjav o tuji pokojnini za odmero letnega dodatka prebere polja s pomočjo obstoječega agentovega API-ja. Samostojna mapa `ld_parser` nima uvozov iz `agent_v6_deep` ali `anonymizer_service` in ne potrebuje njunih diskov ali baze. GUI in neposredni API uporabljata isti postopek in isto Pydantic shemo.

## Zagon

```bash
cd ld_parser
cp .env.example .env  # samo pri prvi namestitvi
# Nastavite AGENT_API_URL in WORKSPACE_API_URL.
docker compose up -d --build
```

GUI: **http://localhost:8086**. Dokumentacija: **http://localhost:8086/docs**. Na gostitelju že tečeta agent-api :8081 in workspace-api :8090; `.env.example` ju doseže prek `host.docker.internal`. V produkciji nastavite njuna dejanska naslova. Agent potrebuje vision model in omogočeno orodje `inspect_pdf`.

GUI podpira obstoječo prijavo pri agentu ter prijavo z veljavnim `guid`. Guid ostane na strežniku, brskalnik dobi naključno HttpOnly cookie. Strojni odjemalci pošljejo **`Authorization: Bearer GUID`**. Ta GUID pridobite z agentovim `POST /v1/auth/login` ali z vašim zunanjim AuthProviderjem; GUID NI modelni/Bifrost API ključ.

## Neposredni JSON klic

```bash
curl --fail-with-body -X POST 'http://localhost:8086/v1/parse?wait_seconds=120' \
  -H "Authorization: Bearer $AGENT_GUID" \
  -F 'files=@example_docs/00000003.tif' \
  -F 'files=@example_docs/00000004.tif'
```

Ko je obdelava končana, **HTTP 200** vrne neposredno objekt z vsemi polji `other_country`, `amount1/currency1/country1` do `amount3/currency3/country3`, `amount/currency/country`, `signature_present` in dodatnimi polji spodaj. Prazen podatek je JSON `null`, ne 0 ali prazen niz. Pri več parih vrne `{ "documents": [{ "pages": [3,4], "source_files": [...], "status": "completed", "data": { ... } }, ...] }`.

`wait_seconds` je 0–300, privzeto 120. Če agent še dela, **HTTP 202** vrne ID in trenutno stanje; glava `Location` kaže na `/v1/jobs/{id}`, `X-Job-ID` vsebuje ID. Nato preverjajte isti ID; ne pošiljajte dokumenta ponovno. Proxy mora dovoliti izbrani čas čakanja; za GUI in integracije s kratkimi HTTP omejitvami uporabite asinhroni način.

```bash
# Takoj vrne HTTP 202, nato lahko preverjate stanje.
curl --fail-with-body -X POST http://localhost:8086/v1/jobs \
  -H "Authorization: Bearer $AGENT_GUID" \
  -F 'files=@example_docs/00000003.tif' -F 'files=@example_docs/00000004.tif'
curl --fail-with-body http://localhost:8086/v1/jobs/ID_OPRAVILA \
  -H "Authorization: Bearer $AGENT_GUID"
```

`GET /v1/jobs` vrne zadnjih 30 paketov prijavljenega uporabnika. Tuji ID vrne 404. Napake vhoda/parjenja vrnejo 422, velikost 413, neprijavljen dostop 401, aktivno LD opravilo istega računa 409. Končna napaka `/v1/parse` vrne 502 z JSON opisom in ID-jem.

## Vedno neparna + naslednja parna

- Ime enostranske datoteke mora biti zaporedna številka strani: `00000187.tif` se poveže samo z `00000188.tif`. Vrstni red nalaganja ni pomemben. Strani 187 in 190 se ne združita.
- Manjkajoč partner, ponovljena številka, ničla in nenumerično ime se zavrnejo pred modelskim klicem.
- Večstranski PDF ali TIFF mora imeti sodo število strani in neparno začetno številko v imenu; npr. `00000187.pdf` s štirimi stranmi pomeni para 187+188 in 189+190.
- Za EN večstranski dokument brez številčnega imena podajte `-F first_page=1`. To polje mora biti pozitivno in neparno. Dve poljubno poimenovani sliki namerno nista samodejno razvrščeni.
- Zaporedne številke vhodnih datotek niso številke zadev/dokumentov v obrazcu.

TIFF, PNG, JPEG in PDF se pretvorijo v rasteriziran **dvostranski PDF na par**. Upoštevata se orientacija in prosojnost; največja stranica je 2400 pik, po potrebi 2000/1600. Slike se stisnejo brez poseganja v izvirnik. Omejitve: 40 MB/datoteko, 100 MB/paket, 40 strani/paket, 50 milijonov pik/vhodno sliko in 8 MB/normalizirani par. Primeri iz `example_docs` se ne kopirajo v Docker sliko.

## Pomen podatkov

- `other_country` je odgovor DA/NE na tujo pokojnino; prebivališče ni dokaz tega odgovora.
- `amount1...3` so tri fizične vrstice na prvi strani. Prazne se ne premikajo; `amount/currency/country` so SAMO dodatna rubrika druge strani, nikoli seštevek.
- `signature_present` preverja podpis upravičenca ali zastopnika na drugi strani. Elektronski podpis izdajatelja, prednatisnjena črta in tiskano ime ne štejejo. Poleg dveh celih strani se agentu pošlje še označen izrez podpisnega območja druge strani za ciljan pregled. Če rubrika v izrezu ni razvidna, je rezultat null z opozorilom; izrez ni dodaten dokument. Pozitivna zaznava zahteva tudi signature_evidence=handwritten. Jasno prazno=false, nečitljivo=null.
- Decimalna vejica se pretvori v decimalno številko; tisočice se odstranijo. Prednatisnjeni primer števk in prazna polja niso zneski. Nenavadne vrednosti se ne popravljajo na »verjetnejše«.
- Valute so dejanske oznake EUR/BAM/RSD/MKD/CHF/AUD/HRK. Zgodovinske HRK se ne pretvarjajo v EUR. Države so ISO alpha-2: D→DE, A→AT, I→IT, AUS→AU, BIH→BA, SRB→RS, MN→ME, MA→MK; CH in HR ostaneta.
- Dodana polja: `document_number` (Št. dok. ob robu), `case_number`, `file_number`, `beneficiary_number` (vsi nizi, ohranijo začetne ničle), `benefit_year`, `statement_date`, `statement_place`, `document_language`, `signature_evidence` (handwritten/blank/uncertain), `needs_review`, `warnings`.
- Leto pomeni referenčno leto pokojnine, ne datuma veljavnosti certifikata. Datum in kraj se prebereta samo iz izpolnjene rubrike. Imena, naslovi in rojstni podatki niso del izhodne sheme.
- Nejasen vpis ali več označenih možnosti → null + opozorilo. Neskladna valuta in država ostaneta takšni, kot sta vpisani, z opozorilom. Neobičajna kombinacija ne pomeni samodejno napake.

## Zanesljivost in hramba

Paket in vsak njegov thread/run se shranita v lastno SQLite bazo v trajnem `/data` volumnu. Prekinjena povezava se ponovno preverja; izgubljen odgovor ob sprejetem opravilu ne sproži podvojene obdelave. Ob ponovnem zagonu LD parser nadaljuje spremljanje. Pri neznanem sprejemu GUI pokaže nepotrjeno stanje. Če agent-api izgubi potrjen run in v threadu ni rezultata, opravilo odpove.

Za vsak par se uporabi ločen thread ter JSON schema. Rezultat se preveri s Pydantic in dopolni z opozorili. Shema preverja strukturo, ne pravilnosti modelskega branja; pred uporabo preverite opozorila in ključne podatke. Vhodni pari na lokalnem disku se izbrišejo po uspehu ali napaki; kopije pri agentu ostanejo v uporabnikovem workspaceu. Baza hrani rezultate in veljavne prijavne tokene (0600); guid opravila se ob zaključku izbriše. Storitev uporablja en worker.

Za produkcijo nastavite HTTPS reverse proxy, `COOKIE_SECURE=true`, `PUBLIC_ORIGIN=https://dejanska-domena`, trajen `/data` ter omejen dostop do volumna in kopij. Agentovih tokenov ne pišite v URL nove storitve; uporabljajte glavo Authorization. `GET /health` preverja samo lokalni proces.

## Testi

```bash
docker compose run --rm -v "$PWD/tests:/tests:ro" -v "$PWD/example_docs:/examples:ro" \
  web python -m unittest discover -s /tests -v
```

Testi preverijo parjenje vseh petih vzorcev, vrstni red, podvojene/manjkajoče strani, večstranske PDF-je, shemo, null/0/false, neposredni JSON, ločitev uporabnikov in izgubljeno potrditev brez ponovnega zagona. API testi uporabljajo simuliranega agenta.

`tests/expected_examples.json` vsebuje ročno pregledane pričakovane vrednosti ključnih polj vzorcev. `tests/live_examples.py` je izrecen živi preizkus, ki kliče model prek agenta; guid bere iz stdin in ga ne izpisuje. Namenjen je okolju z montiranima `/tests` in `/examples` ter dosegljivim LD parserjem na gostiteljevem portu 8086.
