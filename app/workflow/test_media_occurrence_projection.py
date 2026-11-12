"""Media references retain their placement and option ownership during normalization."""
from copy import deepcopy
import unittest

from workflow.wire_format import normalize_item


class MediaOccurrenceProjectionTest(unittest.TestCase):
    media = {"shared.png": "/source-media/example/shared.png"}

    def image(self, placement, option_key=None, **extra):
        attachment = {"kind": "image", "name": "shared.png",
                      "url": self.media["shared.png"], "placement": placement}
        if option_key is not None:
            attachment["optionKey"] = option_key
        attachment.update(extra)
        return attachment

    def test_same_image_in_multiple_options_keeps_each_option(self):
        for options in (["A. [IMG shared.png]", "B. [IMG shared.png]"],
                        [{"key": "A", "text": "[IMG shared.png]"},
                         {"label": "B", "text": "[IMG shared.png]"}]):
            with self.subTest(options=options):
                out = normalize_item({"options": options}, media_map=self.media)
                self.assertEqual(out["options"], ["A.", "B."])
                self.assertEqual(out["attachments"],
                                 [self.image("option", "A"), self.image("option", "B")])

    def test_existing_same_image_keeps_all_placements(self):
        attachments = [self.image("question"), self.image("option", "A"),
                       self.image("answer"), self.image("explanation")]
        out = normalize_item({"attachments": attachments}, media_map=self.media)
        self.assertEqual(out["attachments"], attachments)

    def test_markers_keep_question_answer_and_explanation_ownership(self):
        item = {"stem": "Question [IMG shared.png]", "material": "[IMG shared.png]",
                "answer": "Answer [IMG shared.png]",
                "explanation": "Explanation [IMG shared.png]"}
        out = normalize_item(item, media_map=self.media)
        self.assertEqual(out["stem"], "Question ")
        self.assertEqual(out["material"], "")
        self.assertEqual(out["answer"], "Answer ")
        self.assertEqual(out["explanation"], "Explanation ")
        self.assertEqual(out["attachments"], [self.image("question"), self.image("answer"),
                                              self.image("explanation")])

    def test_existing_attachment_and_same_owner_marker_merge_once(self):
        existing = self.image("option", "A", url="/source-media/original/shared.png")
        item = {"attachments": [existing, deepcopy(existing)],
                "options": ["A. [IMG shared.png] [IMG shared.png]", "B. [IMG shared.png]"]}
        before = deepcopy(item)
        out = normalize_item(item, media_map=self.media)
        self.assertEqual(out["attachments"], [existing, self.image("option", "B")])
        self.assertEqual(item, before)

    def test_existing_attachment_and_different_placement_marker_both_remain(self):
        item = {"attachments": [self.image("question")],
                "explanation": "[IMG shared.png]"}
        out = normalize_item(item, media_map=self.media)
        self.assertEqual(out["attachments"], [self.image("question"), self.image("explanation")])

    def test_existing_unnamed_attachment_matches_same_owner_marker(self):
        existing = self.image("question")
        existing.pop("name")
        out = normalize_item({"attachments": [existing], "stem": "[IMG shared.png]"},
                             media_map=self.media)
        self.assertEqual(out["attachments"], [existing])

    def test_repeated_normalization_is_idempotent_with_children_and_audio(self):
        media = dict(self.media, **{"clip.mp3": "/source-media/example/clip.mp3"})
        audio = {"group": "02_audio/clip.mp3"}
        item = {"unit_id": "group", "unit_kind": "group", "type": "听力短对话理解",
                "stem": "[IMG shared.png]", "answer": "[IMG shared.png]",
                "explanation": "[IMG shared.png]", "attachments": [self.image("question")],
                "options": ["A. [IMG shared.png]", "B. [IMG shared.png]"],
                "subquestions": [{"stem": "[IMG shared.png]", "explanation": "[IMG shared.png]"}]}
        before = deepcopy(item)
        out = normalize_item(item, media_map=media, audio=audio)
        self.assertEqual(len(out["attachments"]), 6)
        self.assertEqual(len(out["subquestions"][0]["attachments"]), 2)
        for _ in range(3):
            repeated = normalize_item(out, media_map=media, audio=audio)
            self.assertEqual(repeated, out)
            out = repeated
        self.assertEqual(item, before)

    def test_parent_and_each_child_keep_independent_occurrences(self):
        item = {"stem": "[IMG shared.png]",
                "subquestions": [{"options": ["A. [IMG shared.png]", "B. [IMG shared.png]"]},
                                 {"options": ["A. [IMG shared.png]", "B. [IMG shared.png]"]}]}
        out = normalize_item(item, media_map=self.media)
        self.assertEqual(out["attachments"], [self.image("question")])
        for child in out["subquestions"]:
            self.assertEqual(child["attachments"],
                             [self.image("option", "A"), self.image("option", "B")])

    def test_audio_duplicates_merge_only_with_same_ownership(self):
        clip = {"kind": "audio", "name": "clip.mp3", "url": "/source-media/example/clip.mp3"}
        explanation = dict(clip, placement="explanation")
        item = {"attachments": [clip, deepcopy(clip), explanation, deepcopy(explanation)]}
        self.assertEqual(normalize_item(item)["attachments"], [clip, explanation])


if __name__ == "__main__":
    unittest.main()
