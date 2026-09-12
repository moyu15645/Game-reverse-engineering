"""LOVELESS localization project - PE helpers and capstone disassembly.

Maps VAs (image base 0x400000) to file offsets and disassembles arbitrary ranges.
"""
import struct
import capstone
import pefile

IMAGE_BASE = 0x400000


class Image:
    def __init__(self, path):
        self.path = path
        self.data = open(path, 'rb').read()
        self.pe = pefile.PE(path, fast_load=True)
        self.sections = []
        for s in self.pe.sections:
            self.sections.append((
                s.Name.rstrip(b'\x00').decode('latin1'),
                s.VirtualAddress, s.Misc_VirtualSize, s.SizeOfRawData,
                s.PointerToRawData))
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.md.detail = False

    def sec_of(self, rva):
        for n, va, vs, rs, ra in self.sections:
            if va <= rva < va + max(vs, rs):
                return (n, va, vs, rs, ra)
        return None

    def off(self, rva):
        s = self.sec_of(rva)
        if not s:
            return None
        n, va, vs, rs, ra = s
        if rva - va >= rs:
            return None
        return ra + (rva - va)

    def read_va(self, va, n):
        rva = va - IMAGE_BASE
        o = self.off(rva)
        if o is None:
            return None
        return self.data[o:o + n]

    def u32(self, va):
        b = self.read_va(va, 4)
        return struct.unpack('<I', b)[0] if b else None

    def dis(self, va, count=64, stop_at=None):
        rva = va - IMAGE_BASE
        o = self.off(rva)
        if o is None:
            return []
        code = self.data[o:o + count]
        out = []
        for ins in self.md.disasm(code, va):
            out.append((ins.address, ins.mnemonic, ins.op_str, ins.size))
            if stop_at and ins.address >= stop_at:
                break
        return out

    def fmt(self, va, count=64, stop_at=None):
        lines = []
        for a, m, o, sz in self.dis(va, count, stop_at):
            lines.append('%08x  %-8s %s' % (a, m, o))
        return lines


if __name__ == '__main__':
    import sys
    img = Image(r'E:\potential\LOVELESS終わらない物語\LOVELESS\Game.exe')
    for x in img.sections:
        print(x)
    if len(sys.argv) > 1:
        va = int(sys.argv[1], 16)
        cnt = int(sys.argv[2]) if len(sys.argv) > 2 else 256
        for l in img.fmt(va, cnt):
            print(l)
