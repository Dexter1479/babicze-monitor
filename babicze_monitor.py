import json
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SHOP_URL = "https://www.babiczecroydon.com/"
STATE_FILE = "babicze_state.json"

EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/138 Safari/537.36"
    )
}


def clean_text(element):
    if not element:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())


def get_products():
    response = requests.get(
        SHOP_URL,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    products = {}

    links = soup.select('a[href*="/product/"]')

    for link in links:
        href = link.get("href")

        if not href:
            continue

        url = urljoin(SHOP_URL, href).split("?")[0]

        # Ten sam produkt może mieć kilka linków na stronie.
        if url in products:
            continue

        name = clean_text(link)

        # Czasami nazwa siedzi w nagłówku wewnątrz linku.
        heading = link.select_one("h1, h2, h3, h4")

        if heading:
            name = clean_text(heading)

        # Jeśli link nie zawiera nazwy, sprawdzamy rodzica.
        parent = link.parent

        if (not name or name.lower() in ["wybierz opcje", "dodaj do koszyka"]) and parent:
            heading = parent.select_one("h1, h2, h3, h4")

            if heading:
                name = clean_text(heading)

        # Cena z najbliższego kontenera produktu.
        price = ""

        container = link.find_parent(
            ["li", "div", "article"]
        )

        if container:
            price_element = (
                container.select_one(".price")
                or container.select_one(".woocommerce-Price-amount")
            )

            if price_element:
                price = clean_text(price_element)

        # Pomijamy linki typu "Wybierz opcje" bez nazwy produktu.
        if not name or name.lower() in [
            "wybierz opcje",
            "dodaj do koszyka"
        ]:
            continue

        products[url] = {
            "name": name,
            "url": url,
            "price": price,
            "sizes": {},
            "overall_stock": None
        }

    return products


def get_product_details(product):
    response = requests.get(
        product["url"],
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    price = (
        soup.select_one("p.price")
        or soup.select_one(".summary .price")
    )

    if price:
        product["price"] = clean_text(price)

    sizes = {}

    form = soup.select_one("form.variations_form")

    if form and form.get("data-product_variations"):
        try:
            variations = json.loads(
                form["data-product_variations"]
            )

            for variation in variations:
                attrs = variation.get("attributes", {})

                size = (
                    attrs.get("attribute_rozmiar")
                    or attrs.get("attribute_pa_rozmiar")
                    or attrs.get("attribute_size")
                    or attrs.get("attribute_pa_size")
                )

                if not size:
                    for value in attrs.values():
                        if value:
                            size = value
                            break

                if size:
                    available = bool(
                        variation.get("is_in_stock", False)
                        and variation.get("is_purchasable", True)
                    )

                    sizes[str(size).upper()] = available

        except Exception:
            pass

    product["sizes"] = sizes

    if soup.select_one(".stock.out-of-stock"):
        product["overall_stock"] = False

    elif (
        soup.select_one(".stock.in-stock")
        or soup.select_one(
            "button.single_add_to_cart_button"
        )
    ):
        product["overall_stock"] = True

    return product


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    with open(
        STATE_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def save_state(products):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            products,
            file,
            ensure_ascii=False,
            indent=2
        )


def send_email(subject, body):
    if not all([
        EMAIL_FROM,
        EMAIL_PASSWORD,
        EMAIL_TO
    ]):
        print("Brak konfiguracji e-mail.")
        return

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = EMAIL_FROM
    message["To"] = EMAIL_TO

    message.set_content(
        "\n".join(body)
    )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465
    ) as smtp:

        smtp.login(
            EMAIL_FROM,
            EMAIL_PASSWORD
        )

        smtp.send_message(message)

    print("E-mail wysłany.")


def compare(old, current):
    for url, product in current.items():

        previous = old.get(url)

        # NOWY PRODUKT
        if previous is None:
            send_email(
                "🔥 NOWY PRODUKT – BABICZE",
                [
                    "Na Babicze Croydon pojawił się nowy produkt!",
                    "",
                    product["name"],
                    "",
                    f"Cena: {product.get('price', '')}",
                    "",
                    product["url"]
                ]
            )

            continue

        # ZMIANA CENY
        old_price = previous.get("price")
        new_price = product.get("price")

        if (
            old_price
            and new_price
            and old_price != new_price
        ):
            send_email(
                "💰 ZMIANA CENY – BABICZE",
                [
                    product["name"],
                    "",
                    f"Stara cena: {old_price}",
                    f"Nowa cena: {new_price}",
                    "",
                    product["url"]
                ]
            )

        # PRODUKT WRÓCIŁ
        if (
            previous.get("overall_stock") is False
            and product.get("overall_stock") is True
        ):
            send_email(
                "✅ PRODUKT ZNÓW DOSTĘPNY – BABICZE",
                [
                    product["name"],
                    "",
                    product["url"]
                ]
            )

        # ROZMIAR WRÓCIŁ
        old_sizes = previous.get("sizes", {})
        new_sizes = product.get("sizes", {})

        available_sizes = []

        for size, available in new_sizes.items():

            if (
                available is True
                and old_sizes.get(size) is not True
            ):
                available_sizes.append(size)

        if available_sizes:

            send_email(
                "📏 ROZMIAR DOSTĘPNY – BABICZE",
                [
                    product["name"],
                    "",
                    "Dostępne rozmiary:",
                    ", ".join(available_sizes),
                    "",
                    product["url"]
                ]
            )


def main():
    print("Sprawdzam Babicze Croydon...")

    send_email(
        "TEST - BABICZE MONITOR",
        [
            "To jest test.",
            "Jeśli widzisz tę wiadomość, powiadomienia działają poprawnie."
        ]
    )


    old = load_state()
    current = get_products()

    print("Znaleziono produktów:", len(current))

    for url, product in current.items():
        try:
            current[url] = get_product_details(product)
        except Exception as error:
            print(
                "Błąd produktu:",
                product["name"],
                error
            )

    if old:
        compare(old, current)
    else:
        print("Pierwsze uruchomienie - zapisuję obecne produkty.")

    save_state(current)

    print("Gotowe.")


if __name__ == "__main__":
    main()
