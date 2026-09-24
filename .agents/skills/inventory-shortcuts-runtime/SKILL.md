---
name: inventory-shortcuts-runtime
description: Runtime testing guidance for inventory keyboard shortcuts against local CRA and Django services.
---

## Environment
Use the repository blueprint for Mongo, Django :8001 and CRA :3000 startup. The local inventory UI requires no login. Test with at least six products because pagination uses five per page.

## Browser checks
- Generate question mark using `shift+slash`, not bulk typing `?`; verify the delivered key if automation behaves unexpectedly.
- Native Chrome autocomplete can consume Escape before the page receives it. Test both with and without an autocomplete popup and distinguish dismissal from canceling the form.
- Arrow keys on the category select legitimately change its value and fetch filtered data; assert that inventory pagination does not change instead of forbidding all requests.
- Fast local refresh requests may complete before a screenshot. Use passive resource timing and a MutationObserver to corroborate requests and the transient Loading text; do not describe DOM instrumentation as visual proof of the loading flash.
- Verify Ctrl+Enter creates a uniquely named product, reload to confirm persistence, then remove only that test product.

## Devin Secrets Needed
None for local UI access. Local Mongo credentials and startup details are in the repository blueprint.
