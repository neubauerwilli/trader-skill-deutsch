import json
import os
import re
import unittest

from _load import ROOT

SKILLS = ["trader-kopie", "trader-nachrichten", "trader-budget", "trader-pelosi", "trader-analyst",
          "trader-filter", "trader-risiko", "trader-broker", "trader-steuer", "trader-alarm",
          "trader-ausgleich"]
ABOUT = ("Elf Claude-Skills auf Deutsch, die die Investoren kopieren, denen du vertraust, die Nachrichten und den US-Kongress im Blick behalten, jeden Trade an dein Budget anpassen und nie einen Auftrag ohne dein Ja abschicken.")
# skills that can lead to an order carry the safety block
ORDER_PATH = ["trader-kopie", "trader-budget", "trader-pelosi", "trader-risiko", "trader-broker",
              "trader-steuer", "trader-alarm", "trader-ausgleich"]


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git"))
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertTrue(text.split("---", 2)[-1].strip(), s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("keine anlageberatung", low, s)
            self.assertIn("ja", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "trader-skill-deutsch")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "trader-skill-deutsch")
        self.assertEqual(market["plugins"][0]["name"], "trader-skill-deutsch")

    def test_readme_documents_the_own_plugin_installation(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# Der Trader-Skill"))
        for line in ("https://github.com/neubauerwilli/trader-skill-deutsch",
                     "/plugin marketplace add neubauerwilli/trader-skill-deutsch",
                     "/plugin install trader-skill-deutsch@trader-skill-deutsch",
                     "## Das Kleingedruckte"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/trader-skill-deutsch:" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("\u2013", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg.get("allow_options", False), False)

    def test_cross_skill_plugin_commands_resolve_to_registered_skills(self):
        for skill in SKILLS:
            _, text = frontmatter(os.path.join(ROOT, skill, "SKILL.md"))
            for target in re.findall(r"/trader-skill-deutsch:(trader-[a-z]+(?:-[a-z]+)*)", text):
                self.assertIn(target, SKILLS, (skill, target))
            for target in SKILLS:
                self.assertNotRegex(text, r"(?<![:\w])/" + re.escape(target) + r"\b")

    def test_money_examples_are_not_claude_positional_templates(self):
        for skill in SKILLS:
            _, text = frontmatter(os.path.join(ROOT, skill, "SKILL.md"))
            self.assertNotRegex(text, r"\$\d", skill)


if __name__ == "__main__":
    unittest.main()
