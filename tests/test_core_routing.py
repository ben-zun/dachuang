import unittest
from backend.agent.core_intent import core_intent


class CoreRoutingTests(unittest.TestCase):
    def test_materials_explanations(self):
        for question in [
            '给我讲一下什么是形成能', '形成能可以判断材料稳定性吗？',
            '介绍几种常见超导材料及其应用条件。', '什么是带隙？',
            '为什么声子有虚频？', '铁电和压电有什么区别？',
            '怎么理解位错与塑性的关系？', '解释一下热电材料的ZT。',
            '磁各向异性有哪些影响？', 'What is density functional theory?',
            'How does diffusion affect grain growth?', '介绍ASE晶胞优化的适用条件',
            '查硅的带隙，并解释PBE低估带隙的原因',
        ]:
            with self.subTest(question=question):
                self.assertIsNotNone(core_intent(question, enhanced=True))

    def test_direct_and_explicit_opt_out(self):
        for question in [
            '你好', '谢谢', '帮我把这句话改短一点', '为什么网站打不开？',
            '查一下硅的带隙', '帮我查一下磷酸铁锂的带隙和稳定性',
            '将-0.7 eV/atom换算为MgCl2的eV/fu', '用Calculate计算2+3',
            '不要查资料，解释什么是形成能', '不用RAG，讲讲超导',
            'Do not search, explain crystal structures',
        ]:
            with self.subTest(question=question):
                self.assertIsNone(core_intent(question))
                self.assertIsNone(core_intent(question, enhanced=True))

    def test_auto_keeps_simple_concepts_optional(self):
        for question in ['什么是形成能？', '什么是带隙？', '铁磁和反铁磁有什么区别？']:
            self.assertIsNone(core_intent(question))
            self.assertIsNotNone(core_intent(question, enhanced=True))
        self.assertIsNotNone(core_intent('结合资料解释形成能'))


if __name__ == '__main__':
    unittest.main()
