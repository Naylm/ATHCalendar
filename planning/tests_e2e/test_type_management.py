import re
import unittest

from playwright.sync_api import expect

from planning.tests_e2e.test_ui import (
    BUREAU_PASSWORD,
    SCREENSHOT_DIR,
    SEED_BUREAU,
    LiveServer,
    TrackedPage,
    browser_tests,
    check_no_horizontal_overflow,
)


class TypeManagementE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if browser_tests() is None:
            raise unittest.SkipTest("playwright non disponible")

    def test_create_with_accented_code_then_delete_unused_type(self):
        with LiveServer() as server, browser_tests()() as pw:
            server.seed(SEED_BUREAU)
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 390, "height": 844},
                    is_mobile=True,
                    has_touch=True,
                )
                tracked = TrackedPage(context.new_page())
                page = tracked.page
                page.goto(server.url + "/bureau/connexion/")
                page.locator('input[name="password"]').fill(BUREAU_PASSWORD)
                page.get_by_role("button", name="Connexion", exact=True).click()
                expect(page).to_have_url(server.url + "/bureau/")
                page.get_by_role("link", name="Types", exact=True).click()

                # Code malformé : le formulaire reste ouvert avec l'erreur et
                # conserve le nom, le code et la couleur saisis.
                add = page.locator("details", has=page.get_by_text("Ajouter un type"))
                add.locator("summary").click()
                add.locator('input[name="name"]').fill("Evènement spécial")
                add.locator('input[name="code"]').fill("a/b")
                add.get_by_label("Sauge", exact=True).check()
                add.get_by_role("button", name="Ajouter", exact=True).click()
                expect(add).to_have_attribute("open", "")
                expect(page.get_by_text("code accepte", exact=False).first).to_be_visible()
                expect(add.locator('input[name="name"]')).to_have_value("Evènement spécial")
                expect(add.locator('input[name="code"]')).to_have_value("a/b")
                expect(add.get_by_label("Sauge", exact=True)).to_be_checked()
                check_no_horizontal_overflow(page)
                page.screenshot(path=str(SCREENSHOT_DIR / "v023-type-create-390.png"))

                # Création valide avec le nom/code demandés.
                add.locator('input[name="code"]').fill("event.spé")
                add.get_by_role("button", name="Ajouter", exact=True).click()
                card = page.locator("li.card", has=page.get_by_text("Evènement spécial"))
                expect(card).to_contain_text("event.spé")
                check_no_horizontal_overflow(page)

                # Suppression : mauvais code refusé, bon code supprime.
                card.get_by_role("link", name="Supprimer", exact=True).click()
                expect(page.get_by_role("heading")).to_contain_text(
                    "Supprimer définitivement le type"
                )
                page.screenshot(path=str(SCREENSHOT_DIR / "v023-type-delete-390.png"))
                page.locator("#id_confirmation_code").fill("mauvais")
                page.locator("#id_confirm_delete").check()
                page.get_by_role("button", name="Supprimer définitivement").click()
                expect(page).to_have_url(re.compile(r"/supprimer/$"))
                expect(page.locator("#id_confirmation_code")).to_have_value("")
                page.locator("#id_confirmation_code").fill("event.spé")
                page.locator("#id_confirm_delete").check()
                page.get_by_role("button", name="Supprimer définitivement").click()
                expect(page).to_have_url(server.url + "/bureau/types/")
                expect(page.locator("li.card code", has_text="event.spé")).to_have_count(0)
                check_no_horizontal_overflow(page)
                tracked.assert_clean()
            finally:
                browser.close()
