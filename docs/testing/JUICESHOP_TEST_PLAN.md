# OWASP Juice Shop Test Plan

Target: `http://127.0.0.1:3000`

This is a safe, non-destructive functional and security-observation checklist for the local Juice Shop container. Security cases verify defensive behavior and access boundaries without attempting destructive exploitation.

| ID | Area | Check | Expected result |
|---|---|---|---|
| JS-001 | Availability | Open the home page | Page loads successfully and product content is visible |
| JS-002 | Availability | Reload the home page | Application remains usable after reload |
| JS-003 | Availability | Open `/rest/products/search?q=juice` | Valid response is returned for product search |
| JS-004 | Catalog | View the first product page | Product name, price, image, and description are visible |
| JS-005 | Catalog | Browse to the next product page | A different product page is displayed |
| JS-006 | Catalog | Use the items-per-page selector | The selected page size is applied |
| JS-007 | Catalog | Use the previous-page control at page one | Control is disabled or has no invalid effect |
| JS-008 | Catalog | Search for an existing product name | Matching products are shown |
| JS-009 | Catalog | Search for a nonsense term | Empty result state is clear and stable |
| JS-010 | Catalog | Search using mixed case | Search behavior is consistent and understandable |
| JS-011 | Catalog | Open product details from an image | Product details open from the image target |
| JS-012 | Catalog | Open product details from the product name | Product details open from the text target |
| JS-013 | Catalog | Add one product to the basket | Basket count increases by one |
| JS-014 | Catalog | Add the same product twice | Quantity/count behavior is consistent and visible |
| JS-015 | Catalog | Add two different products | Both products appear in the basket |
| JS-016 | Basket | Open the basket | Items, quantities, prices, and total are visible |
| JS-017 | Basket | Increase an item quantity | Line total and basket total update correctly |
| JS-018 | Basket | Decrease an item quantity | Quantity and totals update correctly |
| JS-019 | Basket | Remove an item | Item disappears and totals recalculate |
| JS-020 | Basket | Remove the final item | Empty-basket state is shown |
| JS-021 | Basket | Reload with items in the basket | Basket persistence matches the product design |
| JS-022 | Checkout | Attempt checkout while signed out | User is redirected to or prompted for authentication |
| JS-023 | Checkout | Open checkout with an empty basket | Checkout is blocked or explains that items are required |
| JS-024 | Checkout | Add an address with all required fields | Address is accepted and selectable |
| JS-025 | Checkout | Submit an address with missing fields | Field-level validation prevents invalid submission |
| JS-026 | Checkout | Select a saved address | Selected address is visibly applied |
| JS-027 | Checkout | Select a delivery method | Delivery method and cost are reflected in the order summary |
| JS-028 | Checkout | Complete a test purchase with valid data | Order confirmation is shown and order is recorded |
| JS-029 | Checkout | Refresh the order-confirmation page | Confirmation state does not produce a broken page |
| JS-030 | Checkout | Navigate back from confirmation | Back navigation does not duplicate the order |
| JS-031 | Authentication | Open the login page | Login form and validation controls are available |
| JS-032 | Authentication | Submit blank login fields | Required-field validation is shown |
| JS-033 | Authentication | Submit an unknown email/password pair | Login is rejected without exposing sensitive details |
| JS-034 | Authentication | Register with valid unique details | Account creation succeeds with confirmation |
| JS-035 | Authentication | Register with an invalid email | Invalid email is rejected |
| JS-036 | Authentication | Register with a weak password | Password policy feedback is shown |
| JS-037 | Authentication | Register with mismatched confirmation | Submission is rejected with clear feedback |
| JS-038 | Authentication | Register with an existing email | Duplicate account is rejected safely |
| JS-039 | Authentication | Log in with the created account | User becomes authenticated |
| JS-040 | Authentication | Log out | Authenticated controls disappear and session ends |
| JS-041 | Authentication | Use password-reset with an unknown email | Response does not disclose account existence unnecessarily |
| JS-042 | Account | Open the account menu while signed out | Sign-in/register actions are available |
| JS-043 | Account | Open the profile while signed in | Profile page loads the current user's information |
| JS-044 | Account | Update a valid profile field | Update succeeds and persists after reload |
| JS-045 | Account | Submit an invalid profile value | Validation prevents invalid data |
| JS-046 | Feedback | Open the contact page | Contact form and rating controls are visible |
| JS-047 | Feedback | Submit contact form with required valid data | Submission gives clear success feedback |
| JS-048 | Feedback | Submit contact form with missing data | Validation identifies missing requirements |
| JS-049 | Feedback | Enter boundary-length feedback | UI remains usable and applies a clear limit if required |
| JS-050 | Navigation | Open the about page from the menu | About page loads without broken assets |
| JS-051 | Navigation | Open the photo wall | Photo wall loads and images have usable labels/alt text |
| JS-052 | Navigation | Open the tutorial | Tutorial opens and progress/navigation works |
| JS-053 | Navigation | Change the language | Visible labels change or a clear unsupported-language response appears |
| JS-054 | Navigation | Use browser back and forward across catalog/detail/cart | State and routes remain coherent |
| JS-055 | Authorization | Open account-only routes while signed out | Protected routes deny or redirect access |
| JS-056 | Authorization | Open admin-only routes as a normal user | Admin functions are not exposed or executable |
| JS-057 | Authorization | Call a protected API without a session | API returns an appropriate unauthorized response |
| JS-058 | Input safety | Enter HTML-like text in feedback fields | Text is handled as data and does not alter the page |
| JS-059 | Input safety | Enter very long search text | Request completes or fails gracefully without UI corruption |
| JS-060 | Input safety | Use special characters in search and form fields | Application handles encoding safely and remains responsive |
| JS-061 | Accessibility | Navigate the home page using Tab only | Focus order is visible and usable |
| JS-062 | Accessibility | Inspect form labels and accessible names | Inputs and buttons have meaningful accessible names |
| JS-063 | Accessibility | Inspect product images | Informative images have meaningful alt text |
| JS-064 | Accessibility | Trigger a validation error | Error is visible and associated with the relevant field |
| JS-065 | Accessibility | Inspect modal/dialog focus behavior | Focus enters and exits the dialog predictably |
| JS-066 | Responsive UI | Use a narrow mobile viewport | Navigation and product cards remain usable |
| JS-067 | Responsive UI | Use a tablet-width viewport | Content does not overlap or become clipped |
| JS-068 | Performance | Load the home page from a clean session | Initial load completes within the agreed local threshold |
| JS-069 | Performance | Search repeatedly for five terms | No runaway request loop or obvious degradation occurs |
| JS-070 | Resilience | Refresh during catalog navigation | Application recovers to a valid route/state |
| JS-071 | Resilience | Open a nonexistent product route | Clear not-found behavior is shown |
| JS-072 | Resilience | Request an unsupported API method | Server returns a controlled error response |
| JS-073 | Resilience | Observe browser console during core flows | No unexpected uncaught exceptions appear |
| JS-074 | Resilience | Observe network responses during core flows | No unexplained 4xx/5xx responses occur |
| JS-075 | Data integrity | Compare displayed line totals with item price × quantity | Calculations are consistent |
| JS-076 | Data integrity | Compare displayed order total with summary components | Total reconciles with items, delivery, and discounts if present |
| JS-077 | Data integrity | Refresh after profile update | Saved profile data remains consistent |
| JS-078 | Session | Open a second browser session signed out | Session state is isolated from the first session |
| JS-079 | Session | Close and reopen the browser session | Persisted basket/auth behavior matches the product design |
| JS-080 | Operations | Inspect Docker container status and logs | Container remains running without repeated crash/restart behavior |
