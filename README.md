# LinkedIn Vacancies Parser — інструкція користувача

Парсер вакансій LinkedIn на Python + Django + PostgreSQL.
Два незалежні етапи збору, адмін-панель, вивантаження в CSV/Excel.

> **Призначення:** навчальний/тестовий проєкт. Скрапінг LinkedIn заборонений
> їхнім User Agreement — використовуйте на власний ризик і не в промислових
> масштабах. Деталі — в кінці.

---


## 1. Що вміє

| Можливість | Опис |
|---|---|
| Пошук вакансій | ключове слово + країна, кілька запитів одночасно |
| Пагінація | автоматична, з шардінгом за часом (24 год / 7 днів / будь-коли) |
| Деталі вакансії | повний опис, seniority, тип зайнятості, industries, кількість відгуків, зарплата |
| Дані компаній | індустрія, розмір, HQ, рік заснування, спеціалізації, website, about |
| Дедуплікація | одна вакансія = один рядок, незалежно від кількості запитів |
| Перезапуск | прогрес зберігається в БД, робота продовжується з місця зупинки |
| Адмінка | дві секції (Search queries, Vacancies) + дашборд |
| Експорт | CSV та Excel, 29 колонок разом із даними компаній |

---

## 2. Вимоги

- **Python 3.14+**
- **PostgreSQL 18+** (або Docker)
- **Docker Desktop** — для варіанту з контейнерами
- ~500 МБ диску

Перевірка:

```bash
python --version
docker --version        # опційно
psql --version          # опційно
```

---

## 3. Встановлення — Docker (рекомендовано)

```bash
git clone https://github.com/maksymlytvynchyk/Linkedin-scraper.git
cd Linkedin-scraper

cp .env.example .env
# відкрийте .env і задайте DJANGO_SECRET_KEY (див. нижче)

docker compose up -d --build
```

`web` при старті сам застосовує міграції.

**Згенерувати секретний ключ:**

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
# Windows: .venv\Scripts\python.exe -c "..."
```

Вставте результат у `.env`:

```ini
DJANGO_SECRET_KEY=отриманий_ключ
```

**Створити адміністратора:**

```bash
docker compose exec web python manage.py createsuperuser
```

Адмінка: <http://localhost:8000/admin/>

> **Якщо у вас уже працює локальний PostgreSQL на 5432** — не піднімайте
> контейнер `db`, буде конфлікт портів. Запускайте лише веб:
> ```bash
> docker compose up -d --no-deps web
> ```

---

## 4. Встановлення — локально (PostgreSQL)

```bash
git clone https://github.com/maksymlytvynchyk/Linkedin-scraper.git
cd linkedin-jobs-parser

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

**Створіть базу** (один раз, від імені суперюзера `postgres`):

```bash
& "C:\шлях\до\файлу\psql.exe" -U postgres -c "CREATE DATABASE linkedin_jobs OWNER linkedin;"
& "C:\шлях\до\файлу\psql.exe" -U postgres -c "ALTER ROLE linkedin WITH CREATEDB;"
Приклад: "C:\Program Files\PostgreSQL\18\bin\psql.exe"
```

**Пропишіть у `.env`:**

```ini
DJANGO_SECRET_KEY=<згенерований_ключ>
DJANGO_DEBUG=1
DATABASE_URL=postgres://linkedin:<пароль>@localhost:5432/linkedin_jobs
```

**Міграції та користувач:**

```bash
python manage.py migrate
python manage.py createsuperuser
```

---

## 5. Перший запуск

```bash
# 1) стартові пошукові запити (Python Developer / USA)
python manage.py seed_queries

# 2) пробний збір — 2 сторінки
python manage.py parse_search --query-id 1 --max-pages 2 --min-delay 1.0

# 3) деталі для перших 10 вакансій
python manage.py parse_details --limit 10 --with-company

# 4) перевірка
python manage.py export_csv --out out/test.csv
```

Якщо побачили `10 cards` і файл із рядками — все працює.

---

## 6. Як користуватись адмінкою

`http://localhost:8000/admin/`, дашборд — `/admin/dashboard/`.

### Search queries

Створення запиту: **Add** → поля:

| Поле | Приклад | Примітка |
|---|---|---|
| Keywords | `Python Developer` | обов'язкове |
| Country | `United States` | зі списку або вручну |
| Geo ID | *(порожньо)* | заповниться автоматично |
| Posted / Remote / Experience | *(порожньо)* | опційні фільтри |

Кілька запитів з різними країнами — просто додайте ще один.

На списку видно: статус, прочитано карток, знайдено вакансій, стан шардів.
Дії: **Reset pagination progress**, **Mark as done**, **Re-queue their vacancies**.

### Vacancies

Фільтри за статусом/компанією/типом зайнятості, пошук по опису.

| Статус | Значення |
|---|---|
| `pending` | картка зібрана, деталі ще ні |
| `fetched` | повністю збагачена |
| `failed` | помилка (див. поле last error) |
| `gone` | вакансія закрита/видалена |

Дії: **Re-queue for detail parsing**, **Mark as gone (expired)**.

### Dashboard

Загальні лічильники, топ компаній і локацій, розподіл за статусами.

---

## 7. Запуск парсера

### Етап 1 — пошук

```bash
python manage.py parse_search --all
```

| Прапорець | Призначення |
|---|---|
| `--all` | усі збережені запити |
| `--query-id 1` | конкретний запит (можна кілька разів) |
| `--keywords "..." --country "..."` | разовий запуск без збереженого запиту |
| `--max-pages N` | бюджет сторінок на запуск (10 карток/стор.) |
| `--max-inserts N` | зупинитись після N нових вакансій |
| `--posted r86400` | тільки за добу; `r604800` — тиждень; `r2592000` — місяць |
| `--fresh` | скинути курсори й почати заново |
| `--min-delay 1.0` | пауза між запитами (сек) |
| `--stop-on-block` | зупинитись при блокуванні |

### Етап 2 — деталі + компанії

```bash
python manage.py parse_details --all --with-company
```

| Прапорець | Призначення |
|---|---|
| `--limit 100` | максимум вакансій за запуск |
| `--all` | усі `pending` |
| `--retry-failed` | повторити невдалі |
| `--with-company` | тягнути сторінки компаній |
| `--max-attempts 5` | не чіпати рядки з більшою кількістю спроб |

> **Увага, Windows/PowerShell:** `--posted ""` (порожній рядок = «без фільтра»)
> не передається через shell. Використовуйте `--posted=` або просто не
> вказуйте прапорець (типово: будь-коли + 7 днів + 24 години).

### Скільки це триває

10 карток = 1 запит ≈ 1 с при `--min-delay 0.8`.
10 000 вакансій ≈ 1 000 запитів пошуку + 10 000 запитів деталей.

Для довгих прогонів — у фоні з логом:

```bash
python manage.py parse_search --all --min-delay 1.0 > out/search.log 2>&1 &
# Docker:
docker compose run --rm -d web python manage.py parse_search --all --min-delay 1.0
```

---

## 8. Експорт результатів

```bash
python manage.py export_csv  --out out/vacancies.csv
python manage.py export_xlsx --out out/vacancies.xlsx
```


