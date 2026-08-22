import tempfile
import threading
import time
import unittest
from pathlib import Path

from phone_harness.operator_broker import (
    HumanTeachingInbox,
    OperatorQuestionBroker,
    load_operator_presence,
    load_pending_question,
    set_operator_presence,
)
from phone_harness.trace import TraceSink, read_trace_events


class OperatorBrokerTests(unittest.TestCase):
    def test_teaching_can_be_recorded_as_nonblocking_learning_input(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = HumanTeachingInbox(
                message_dir=Path(directory) / "teaching",
                attachment_dir=Path(directory) / "attachments",
            )
            item = inbox.submit(
                "late answer",
                source="operator_question_answer",
                blocking=False,
            )
            loaded = inbox.get(item["message_id"])
            self.assertEqual(loaded["source"], "operator_question_answer")
            self.assertFalse(loaded["blocking"])

    def test_question_preview_is_copied_to_durable_history_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "frame.png"
            source.write_bytes(b"fake-png")
            broker = OperatorQuestionBroker(
                pending_file=root / "operator" / "pending.json",
                response_dir=root / "operator" / "responses",
                question_dir=root / "operator" / "questions",
            )
            item = broker.post("What is this?", preview_path=source)
            copied = Path(item["preview_path"])
            self.assertTrue(copied.is_file())
            self.assertNotEqual(copied, source)
            source.unlink()
            self.assertTrue(copied.is_file())

    def test_presence_defaults_to_present_and_persists_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presence.json"
            self.assertTrue(load_operator_presence(path)["present"])
            set_operator_presence(False, path=path)
            self.assertFalse(load_operator_presence(path)["present"])

    def test_absent_operator_keeps_question_pending_for_late_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "events.jsonl"
            presence = root / "presence.json"
            pending = root / "pending.json"
            set_operator_presence(False, path=presence)
            broker = OperatorQuestionBroker(
                trace=TraceSink("test", path=trace_path),
                pending_file=pending,
                response_dir=root / "responses",
                presence_file=presence,
            )
            self.assertIsNone(broker.ask("Which item?", timeout=0.1))
            self.assertTrue(pending.exists())
            question = load_pending_question(pending)
            self.assertEqual(question["status"], "pending")
            self.assertEqual(read_trace_events(trace_path)[-1]["type"], "operator.question")

    def test_timed_out_question_remains_answerable_and_can_be_consumed_later(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response_dir = root / "responses"
            pending = root / "pending.json"
            broker = OperatorQuestionBroker(
                pending_file=pending,
                response_dir=response_dir,
                presence_file=root / "presence.json",
            )
            set_operator_presence(True, path=root / "presence.json")
            question = broker.post("What appeared after the merge?", promote_answer_to_teaching=True)
            self.assertIsNone(broker.wait(question["question_id"], timeout=0.02, poll_interval=0.01))
            self.assertEqual(load_pending_question(pending)["question_id"], question["question_id"])
            OperatorQuestionBroker.submit_response(
                question["question_id"],
                "A green cash item appeared",
                response_dir=response_dir,
            )
            consumed = broker.consume_responses()
            self.assertEqual(consumed[0]["response"]["answer"], "A green cash item appeared")
            self.assertFalse(pending.exists())
            self.assertEqual(broker.get(question["question_id"])["status"], "consumed")

    def test_question_answer_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "events.jsonl"
            response_dir = root / "responses"
            pending = root / "pending.json"
            broker = OperatorQuestionBroker(
                trace=TraceSink("test", path=trace_path),
                pending_file=pending,
                response_dir=response_dir,
                presence_file=root / "presence.json",
            )
            set_operator_presence(True, path=root / "presence.json")
            question = broker.post(
                "Which icon is correct?",
                choices=("A", "B"),
                confidence=0.55,
                impact="high",
            )
            self.assertEqual(load_pending_question(pending)["question_id"], question["question_id"])
            OperatorQuestionBroker.submit_response(
                question["question_id"],
                "B is correct",
                choice="B",
                response_dir=response_dir,
            )
            response = broker.wait(question["question_id"], timeout=1)
            self.assertEqual(response["answer"], "B is correct")
            self.assertFalse(pending.exists())
            self.assertEqual([event["type"] for event in read_trace_events(trace_path)], [
                "operator.question", "operator.answer"
            ])

    def test_wait_can_receive_async_gui_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broker = OperatorQuestionBroker(
                pending_file=root / "pending.json",
                response_dir=root / "responses",
                presence_file=root / "presence.json",
            )
            set_operator_presence(True, path=root / "presence.json")
            question = broker.post("Continue?")

            def answer():
                time.sleep(0.05)
                OperatorQuestionBroker.submit_response(
                    question["question_id"], "yes", response_dir=root / "responses"
                )

            worker = threading.Thread(target=answer)
            worker.start()
            response = broker.wait(question["question_id"], timeout=1, poll_interval=0.02)
            worker.join()
            self.assertEqual(response["answer"], "yes")

    def test_operator_can_submit_teaching_with_image_and_receive_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "events.jsonl"
            inbox = HumanTeachingInbox(
                trace=TraceSink("test", path=trace_path),
                message_dir=root / "teaching",
                attachment_dir=root / "attachments",
            )
            message = inbox.submit(
                "This is the correct item.",
                image_bytes=b"fake-png",
                image_mime_type="image/png",
                image_name="item.png",
            )
            pending = inbox.next_pending()
            self.assertEqual(pending["message_id"], message["message_id"])
            self.assertEqual(Path(pending["image_path"]).read_bytes(), b"fake-png")
            handled = inbox.handle(message["message_id"], reply="Learned.", learned=True)
            self.assertEqual(handled["status"], "handled")
            self.assertEqual(handled["reply"], "Learned.")
            self.assertTrue(handled["learned"])
            self.assertIsNone(inbox.next_pending())
            self.assertEqual([event["type"] for event in read_trace_events(trace_path)], [
                "operator.message", "operator.message.handled"
            ])

    def test_teaching_requires_text_or_supported_image(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = HumanTeachingInbox(message_dir=Path(directory) / "teaching")
            with self.assertRaises(ValueError):
                inbox.submit("")
            with self.assertRaises(ValueError):
                inbox.submit("x", image_bytes=b"x", image_mime_type="image/gif")


if __name__ == "__main__":
    unittest.main()
