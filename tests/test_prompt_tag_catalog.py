"""Regression checks for the general-purpose prompt catalog and its workflows."""

from __future__ import annotations

import unittest

from imaginarium_forge.application.services import prompt_tag_builder_service as service
from imaginarium_forge.domain.character.gender import CharacterGender


class PromptTagCatalogTests(unittest.TestCase):
    def test_catalog_shape_and_wardrobe_slots(self) -> None:
        catalogs = (
            service.CHARACTER_CATEGORIES,
            service.BACKGROUND_CATEGORIES,
            service.NEGATIVE_CATEGORIES,
        )
        category_keys = [category.key for catalog in catalogs for category in catalog]
        self.assertEqual(len(category_keys), len(set(category_keys)))
        clothing_slots = {
            category.key
            for category in service.CHARACTER_CATEGORIES
            if category.group == "服裝與配件"
        }
        self.assertEqual(
            clothing_slots,
            {
                "outfit_archetype", "outfit_materials", "outfit_palette", "accessories",
                "outfit_upper", "outfit_lower", "outfit_one_piece", "outfit_outerwear",
                "outfit_sleepwear", "outfit_uniform_sport", "outfit_swimwear",
                "accessory_head_hair", "accessory_face_neck", "accessory_hand_arm",
                "accessory_waist_body", "accessory_bags", "hosiery_style", "hosiery_length",
                "footwear", "outfit_upper_state", "outfit_lower_state",
            },
        )

    def test_random_character_prompts_across_identity_modes(self) -> None:
        for gender in CharacterGender:
            for mode in service.CHARACTER_IDENTITY_MODES:
                categories = tuple(
                    category
                    for category in service.filter_character_categories_by_identity(
                        service.CHARACTER_CATEGORIES, mode
                    )
                    if category.applicable_gender in (None, gender)
                )
                species_key = {
                    "beast_humanoid": "beast_humanoid_species",
                    "furry": "furry_species",
                }.get(mode)
                for seed in range(8):
                    with self.subTest(gender=gender, mode=mode, seed=seed):
                        selections = service.randomize_selections(
                            categories,
                            fill_blanks_only=False,
                            seed=seed,
                            locked_selections={species_key: ("dog",)} if species_key else {},
                        )
                        prompt = service.build_character_prompt(
                            gender, selections, identity_mode=mode
                        )
                        self.assertTrue(prompt.startswith(gender.prompt_token))
                        self.assertTrue(prompt.isascii())
                        self.assertTrue(
                            selections.get("outfit_upper") and selections.get("outfit_lower")
                            or selections.get("outfit_one_piece")
                            or selections.get("outfit_sleepwear")
                            or selections.get("outfit_swimwear")
                        )

    def test_manual_wardrobe_colors_and_state_precedence(self) -> None:
        selections = {
            "outfit_upper": ("shirt",),
            "outfit_lower": ("trousers",),
            "outfit_upper_state": ("upper_state_buttoned",),
            "hair_color": ("black", "silver"),
            "hair_color_pattern": ("split_dye",),
            "expression": ("cheerful",),
            "character_state": ("asleep",),
            "pose": "seated",
        }
        prompt = service.build_character_prompt(CharacterGender.FEMALE, selections)
        self.assertIn("shirt", prompt)
        self.assertIn("trousers", prompt)
        self.assertIn("buttoned upper garment", prompt)
        self.assertIn("black", prompt)
        self.assertIn("silver", prompt)
        self.assertIn("peacefully asleep", prompt)
        self.assertNotIn("cheerful expression", prompt)

    def test_random_fill_preserves_manual_selections(self) -> None:
        categories = service.filter_character_categories_by_identity(
            service.CHARACTER_CATEGORIES, "standard"
        )
        current = {"hair_color": ("black",), "outfit_upper": ("shirt",)}
        selections = service.randomize_selections(categories, current=current, seed=12)
        self.assertEqual(selections["hair_color"], ("black",))
        self.assertEqual(selections["outfit_upper"], ("shirt",))
        self.assertTrue(selections.get("outfit_lower"))

    def test_background_negative_and_combined_prompts(self) -> None:
        for seed in range(8):
            with self.subTest(seed=seed):
                background = service.randomize_selections(
                    service.BACKGROUND_CATEGORIES, fill_blanks_only=False, seed=seed
                )
                negative = service.randomize_selections(
                    service.NEGATIVE_CATEGORIES, fill_blanks_only=False, seed=seed
                )
                self.assertTrue(service.build_background_prompt(background))
                self.assertTrue(service.build_negative_prompt(negative))
                combined = service.build_combined_scene_prompt(
                    "male",
                    {"outfit_archetype": "scholar", "framing": "full_body"},
                    background,
                )
                self.assertIn("scholar", combined)
                self.assertTrue(combined.startswith(CharacterGender.MALE.prompt_token))

    def test_sheet_custom_species_and_invalid_inputs(self) -> None:
        sheet = service.build_character_prompt(
            "female",
            {
                "character_output_purpose": "complete_character_sheet",
                "pose": "running",
                "outfit_archetype": "formal",
            },
        )
        self.assertNotIn("running pose", sheet)
        custom = service.build_character_prompt(
            "male",
            {"beast_humanoid_species": ("other",)},
            identity_mode="beast_humanoid",
            custom_species_en="snow leopard",
        )
        self.assertIn("snow leopard", custom)
        with self.assertRaises(ValueError):
            service.build_character_prompt("female", {"unknown_category": "unknown"})
        with self.assertRaises(ValueError):
            service.build_character_prompt("female", {"outfit_upper": ("unknown",)})
        with self.assertRaises(ValueError):
            service.build_character_prompt(
                "female", {"outfit_upper_state": ("upper_state_buttoned",)}
            )


if __name__ == "__main__":
    unittest.main()
