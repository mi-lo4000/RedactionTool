# RedactionTool 🛡️

Et letvægts desktop-værktøj skrevet i Python til automatiseret batch-redigering (overstregning) af følsomme oplysninger i PDF-dokumenter.

Værktøjet er designet til at finde specifikke tekstmønstre (via RegEx eller eksakt match) og dække dem med sorte bokse, så følsomme data som totalbeløb, ID-numre eller navne fjernes permanent.

## ✨ Funktioner

* **Batch-behandling:** Rediger en hel mappe med PDF'er med ét klik.
* **Smart Preview:** Se en tilfældig PDF fra din mappe med ændringerne anvendt (i hukommelsen), så du kan verificere indstillingerne før kørsel.
* **Mønsterbaseret:** Understøtter både **Regular Expressions (RegEx)** og **Eksakt tekst** matching.
* **Custom Padding:** Finjuster størrelsen på de sorte bokse for at sikre fuld dækning af teksten.
* **Dansk brugerflade:** Enkel og intuitiv GUI bygget med Tkinter.

## 🚀 Kom i gang

### Forudsætninger

Du skal have Python 3.8+ installeret. Værktøjet bruger `PyMuPDF` (fitz) til PDF-manipulation.

### Installation

1.  Klon dette repository:
    ```bash
    git clone [https://github.com/mi-lo4000/RedactionTool.git](https://github.com/mi-lo4000/RedactionTool.git)
    cd RedactionTool
    ```

2.  Installer afhængigheder:
    ```bash
    pip install -r requirements.txt
    ```

3.  Kør applikationen:
    ```bash
    python main.py
    ```

## 🛠 Konfiguration

For at ændre hvilke oplysninger der skal fjernes, skal du redigere listen `COLUMNS_TO_REDACT` i `main.py`.

Hver regel indeholder:
* `name`: Navnet på reglen (vises i loggen).
* `redact_text`: Teksten eller det RegEx-mønster der skal findes.
* `use_regex`: Sættes til `True` for mønstre eller `False` for almindelig tekst.
* `text_pad`: Juster boksens størrelse `(Venstre, Top, Højre, Bund)`.

## 📦 Byg til EXE

Hvis du vil lave en selvstændig Windows-fil (.exe), kan du bruge den medfølgende batch-fil:
```bash
build_exe.bat