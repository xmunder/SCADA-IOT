from gateway.buffer import StoreAndForwardBuffer


def test_fifo_order_and_ack(tmp_path):
    buf = StoreAndForwardBuffer(tmp_path / "b.db")
    ids = [buf.append(f"m{i}".encode(), i) for i in range(5)]

    rows = buf.peek(3)
    assert [payload for _, payload in rows] == [b"m0", b"m1", b"m2"]

    buf.ack([ids[0], ids[1]])
    assert buf.pending() == 3
    assert [p for _, p in buf.peek(10)] == [b"m2", b"m3", b"m4"]
    buf.close()


def test_messages_survive_reopen(tmp_path):
    path = tmp_path / "b.db"
    buf = StoreAndForwardBuffer(path)
    buf.append(b"pendiente", 1)
    buf.close()

    reopened = StoreAndForwardBuffer(path)
    assert reopened.pending() == 1
    assert reopened.peek(1)[0][1] == b"pendiente"
    reopened.close()


def test_ack_empty_list_is_noop(tmp_path):
    buf = StoreAndForwardBuffer(tmp_path / "b.db")
    buf.ack([])
    assert buf.pending() == 0
    buf.close()
