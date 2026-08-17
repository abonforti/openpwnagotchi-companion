"""The device model the `smbus2` in this stub talks to.

Not part of either library's public surface: this is the test scaffolding that
lets a test say what is on the bus and then read back what the plugin did with
it. Everything here is underscore-free only because the whole module is private
by virtue of its name; the plugin must never import it, and on a real unit it
does not exist.

The register map is addressed by `(i2c_address, register)` so that a read of the
wrong address cannot accidentally answer with the right value. Unset registers
answer `default_byte` rather than raising: a PiSugar answers on far more
registers than SPEC 2.11 pins, and a stub that raised on the ones it does not
model would turn "the plugin also read the charging register" into a spurious
failure of the percentage path.
"""

from __future__ import annotations


class FakeI2CDevice:
    """One I2C device, plus a recording of every interaction with the bus."""

    def __init__(self) -> None:
        self.registers: dict[tuple[int, int], int] = {}
        self.default_byte = 0x00
        #: Every bus number `SMBus(...)` was constructed or opened with.
        self.opened: list[int | str | None] = []
        #: Every `(address, register)` read, in order.
        self.reads: list[tuple[int, int]] = []
        #: Every `(address, register, value)` written, in order.
        self.writes: list[tuple[int, int, int]] = []
        self.closed = 0
        #: Raised by the constructor and by `open()`; the bus that is not there.
        self.open_error: BaseException | None = None
        #: Raised by every read; the bus that is there and does not answer.
        self.read_error: BaseException | None = None

    def _reset(self) -> None:
        self.__init__()  # noqa: PLC2801 - one definition of "empty", not two

    def byte(self, address: int, register: int) -> int:
        self.reads.append((address, register))
        if self.read_error is not None:
            raise self.read_error
        return self.registers.get((address, register), self.default_byte) & 0xFF

    def read_addresses(self) -> set[int]:
        return {address for address, _ in self.reads}

    def read_registers(self, address: int) -> list[int]:
        return [register for read_address, register in self.reads if read_address == address]


#: The single device every `SMBus` in this stub is wired to. A test mutates it
#: through the `i2c_stub()` helper in `tests/test_deps.py`, which resets it.
device = FakeI2CDevice()


class SMBus:
    """The subset of the `smbus2` bus object a byte read needs.

    Deliberately permissive about *how* the read is made - byte, word or block,
    constructor or `open()`, context manager or not - because SPEC 2.11 pins the
    bus, the address and the register and says nothing about the call. What the
    stub records is the bus, the address and the register, which is exactly the
    part that is pinned.
    """

    def __init__(self, bus: int | str | None = None, force: bool = False) -> None:
        self.address = None
        self.force = force
        if bus is not None:
            self.open(bus)

    def open(self, bus: int | str) -> None:
        device.opened.append(bus)
        if device.open_error is not None:
            raise device.open_error

    def close(self) -> None:
        device.closed += 1

    def __enter__(self) -> "SMBus":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False

    # -- reads -------------------------------------------------------------

    def read_byte_data(self, i2c_addr: int, register: int, force: bool | None = None) -> int:
        return device.byte(i2c_addr, register)

    def read_word_data(self, i2c_addr: int, register: int, force: bool | None = None) -> int:
        low = device.byte(i2c_addr, register)
        high = device.byte(i2c_addr, register + 1)
        return low | (high << 8)

    def read_i2c_block_data(
        self, i2c_addr: int, register: int, length: int = 32, force: bool | None = None
    ) -> list[int]:
        return [device.byte(i2c_addr, register + offset) for offset in range(length)]

    def read_byte(self, i2c_addr: int, force: bool | None = None) -> int:
        return device.byte(i2c_addr, 0x00)

    # -- writes ------------------------------------------------------------

    def write_byte_data(
        self, i2c_addr: int, register: int, value: int, force: bool | None = None
    ) -> None:
        device.writes.append((i2c_addr, register, value))
        device.registers[(i2c_addr, register)] = value & 0xFF

    def write_quick(self, i2c_addr: int, force: bool | None = None) -> None:
        device.writes.append((i2c_addr, 0x00, 0x00))

    # -- raw transfers -----------------------------------------------------

    def i2c_rdwr(self, *messages: "i2c_msg") -> None:
        for message in messages:
            message._transfer()


class i2c_msg:  # noqa: N801 - the real smbus2 spells it this way
    """`smbus2`'s raw message, enough of it for a register-then-read exchange."""

    def __init__(self, address: int, read: bool, length: int, data: bytes = b"") -> None:
        self.addr = address
        self.is_read = read
        self.len = length
        self.buf = bytearray(data)
        self._register = 0

    @classmethod
    def read(cls, address: int, length: int) -> "i2c_msg":
        return cls(address, True, length)

    @classmethod
    def write(cls, address: int, data: bytes) -> "i2c_msg":
        message = cls(address, False, len(data), bytes(data))
        if message.buf:
            message._register = message.buf[0]
        return message

    def _transfer(self) -> None:
        if self.is_read:
            self.buf = bytearray(
                device.byte(self.addr, self._register + offset) for offset in range(self.len)
            )
        else:
            device.writes.extend(
                (self.addr, self._register, value) for value in self.buf
            )

    def __iter__(self):
        return iter(self.buf)

    def __len__(self) -> int:
        return len(self.buf)
