# OWASP Juice Shop Test Report

Date: 2026-09-16  
Target: `http://127.0.0.1:3000`  
Container: `holoqa-juiceshop`  
Image: `bkimminich/juice-shop:latest`  
Observed application version: `v20.2.0`

## Environment result

- Docker container is running and mapped to `0.0.0.0:3000->3000/tcp`.
- Home page returned HTTP 200.
- Container startup checks completed successfully.
- Container logs report optional challenge warnings for missing `ALCHEMY_API_KEY` and unavailable local LLM service. These affect only the related Juice Shop challenges.

## Executed checks

| ID | Result | Observation | Evidence |
|---|---|---|---|
| JS-001 | PASS | Home page loaded with Juice Shop branding and product UI | [initial.png](juice-shop-output/screenshots/initial.png) |
| JS-002 | PASS | Reload returned a usable application shell | [catalog.png](juice-shop-output/screenshots/catalog.png) |
| JS-008 | PASS | Search for `apple` returned three matching products after submitting the search | [search-apple.png](juice-shop-output/screenshots/search-apple.png) |
| JS-009 | NOT RUN | Nonsense-search behavior remains in the checklist | — |
| JS-013 | PASS | Product was added to the anonymous basket; basket count became 1 | [basket-working.png](juice-shop-output/screenshots/basket-working.png) |
| JS-016 | PASS | Basket showed product, quantity, price, total, and checkout control | [basket-working.png](juice-shop-output/screenshots/basket-working.png) |
| JS-022 | PASS | Anonymous basket displayed checkout as disabled, enforcing the sign-in boundary | [basket.png](juice-shop-output/screenshots/basket.png) |
| JS-031 | PASS | Login page exposed labeled email/password fields and login controls | [login.png](juice-shop-output/screenshots/login.png) |
| JS-032 | PASS | Login button was disabled until required fields were populated | [login.png](juice-shop-output/screenshots/login.png) |
| JS-034 | PASS | Registration accepted valid test details and showed successful registration feedback | [register-page.png](juice-shop-output/screenshots/register-page.png) |
| JS-039 | PASS | Newly registered test account logged in and returned to the catalog | [authenticated.png](juice-shop-output/screenshots/authenticated.png) |
| JS-050 | PASS | About route opened successfully from the observed navigation route | [about.png](juice-shop-output/screenshots/about.png) |
| JS-051 | PASS | Photo Wall route opened successfully from the observed navigation route | [photo-wall.png](juice-shop-output/screenshots/photo-wall.png) |
| JS-056 | PASS | Normal user opening administration received `403 You are not allowed to access this page!` | [admin-boundary.png](juice-shop-output/screenshots/admin-boundary.png) |
| JS-062 | PASS | Login and registration controls exposed meaningful accessible names in snapshots | [login.png](juice-shop-output/screenshots/login.png) |
| JS-073 | PASS | No unexpected console exception was returned during the exercised core flows | Session console checks |
| JS-074 | PASS | No unexplained failed network request was observed during the exercised core flows | Session network checks |
| JS-080 | PASS | Container remained running throughout testing with no crash loop observed | Docker status/logs |

## Findings

No confirmed application defect was recorded from the executed checks. The two startup warnings are environment configuration notices for optional Juice Shop challenge integrations, not failures of the core storefront flow.

The complete 80-case coverage plan, including unexecuted negative, accessibility, responsive, performance, resilience, and data-integrity cases, is in [JUICESHOP_TEST_PLAN.md](JUICESHOP_TEST_PLAN.md).
