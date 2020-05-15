# Copyright 2017 Neural Networks and Deep Learning lab, MIPT
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from logging import getLogger
from typing import Tuple, List, Any, Optional, Union

import re
import nltk

from deeppavlov.core.common.registry import register
from deeppavlov.core.models.component import Component
from deeppavlov.core.models.serializable import Serializable
from deeppavlov.models.kbqa.template_matcher import TemplateMatcher
from deeppavlov.models.kbqa.entity_linking import EntityLinker
from deeppavlov.models.kbqa.wiki_parser import WikiParser
from deeppavlov.models.kbqa.rel_ranking_infer import RelRankerInfer
from deeppavlov.models.kbqa.rel_ranking_bert_infer import RelRankerBertInfer
from deeppavlov.models.kbqa.utils import extract_year, extract_number, asc_desc, make_entity_combs

log = getLogger(__name__)


@register('query_generator')
class QueryGenerator(Component, Serializable):
    """
        This class takes as input entity substrings, defines the template of the query and
        fills the slots of the template with candidate entities and relations.
    """

    def __init__(self, template_matcher: TemplateMatcher,
                 linker: EntityLinker,
                 wiki_parser: WikiParser,
                 rel_ranker: Union[RelRankerInfer, RelRankerBertInfer],
                 load_path: str,
                 rank_rels_filename_1: str,
                 rank_rels_filename_2: str,
                 entities_to_leave: int = 5,
                 rels_to_leave: int = 10,
                 rels_to_leave_2hop: int = 7,
                 return_answers: bool = False, **kwargs) -> None:
        """

        Args:
            template_matcher: component deeppavlov.models.kbqa.template_matcher
            linker: component deeppavlov.models.kbqa.entity_linking
            wiki_parser: component deeppavlov.models.kbqa.wiki_parser
            rel_ranker: component deeppavlov.models.kbqa.rel_ranking_infer
            load_path: path to folder with wikidata files
            rank_rels_filename_1: file with list of rels for first rels in questions with ranking 
            rank_rels_filename_2: file with list of rels for second rels in questions with ranking
            entities_to_leave: how many entities to leave after entity linking
            rels_to_leave: how many relations to leave after relation ranking
            rels_to_leave_2hop: how many relations to leave in 2-hop questions
            return_answers: whether to return answers or candidate answers
            **kwargs:
        """
        super().__init__(save_path=None, load_path=load_path)
        self.template_matcher = template_matcher
        self.linker = linker
        self.wiki_parser = wiki_parser
        self.rel_ranker = rel_ranker
        self.rank_rels_filename_1 = rank_rels_filename_1
        self.rank_rels_filename_2 = rank_rels_filename_2
        self.entities_to_leave = entities_to_leave
        self.rels_to_leave = rels_to_leave
        self.rels_to_leave_2hop = rels_to_leave_2hop
        self.return_answers = return_answers
        self.load()

    def load(self) -> None:
        with open(self.load_path / self.rank_rels_filename_1, 'r') as fl1:
            lines = fl1.readlines()
            self.rank_list_0 = [line.split('\t')[0] for line in lines]

        with open(self.load_path / self.rank_rels_filename_2, 'r') as fl2:
            lines = fl2.readlines()
            self.rank_list_1 = [line.split('\t')[0] for line in lines]

    def save(self) -> None:
        pass

    def __call__(self, question_batch: List[str],
                 template_type_batch: List[str],
                 entities_from_ner_batch: List[List[str]]) -> List[Tuple[str]]:

        candidate_outputs_batch = []
        for question, template_type, entities_from_ner in \
                     zip(question_batch, template_type_batch, entities_from_ner_batch):

            candidate_outputs = []
            self.template_num = int(template_type)

            replace_tokens = [(' - ', '-'), (' .', ''), ('{', ''), ('}', ''), ('  ', ' '), ('"', "'"), ('(', ''), (')', '')]
            for old, new in replace_tokens:
                question = question.replace(old, new)

            print("question after sanitize", question)

            entities_from_template, rels_from_template, query_type_template = self.template_matcher(question)
            if query_type_template.isdigit():
                self.template_num = int(query_type_template)

            log.debug(f"question: {question}\n")
            log.debug(f"template_type {self.template_num}")

            if entities_from_template:
                log.debug(f"(__call__)entities_from_template: {entities_from_template}")
                entity_ids = self.get_entity_ids(entities_from_template)
                log.debug(f"entities_from_template {entities_from_template}")
                log.debug(f"rels_from_template {rels_from_template}")
                log.debug(f"entity_ids {entity_ids}")

                candidate_outputs = self.find_candidate_answers(question, entity_ids, rels_from_template)

            if not candidate_outputs and entities_from_ner:
                log.debug(f"(__call__)entities_from_ner: {entities_from_ner}")
                entity_ids = self.get_entity_ids(entities_from_ner)
                log.debug(f"(__call__)entity_ids: {entity_ids}")
                log.debug(f"entities_from_ner {entities_from_ner}")
                log.debug(f"entity_ids {entity_ids}")
                log.debug(f"(__call__)self.template_num: {self.template_num}")
                log.debug(f"(__call__)template_type: {template_type}")
                self.template_num = int(template_type[0])
                log.debug(f"(__call__)self.template_num: {self.template_num}")
                candidate_outputs = self.find_candidate_answers(question, entity_ids, rels_from_template=None)
            candidate_outputs_batch.append(candidate_outputs)
        if self.return_answers:
            answers = self.rel_ranker(question_batch, candidate_outputs_batch)
            log.debug(f"(__call__)answers: {answers}")
            return answers
        else:
            log.debug(f"(__call__)candidate_outputs_batch: {candidate_outputs_batch}")
            return candidate_outputs_batch

    def get_entity_ids(self, entities: List[str]) -> List[List[str]]:
        entity_ids = []
        for entity in entities:
            entity_id, confidences = self.linker(entity)
            entity_ids.append(entity_id[:15])
        return entity_ids

    def find_candidate_answers(self, question: str,
                               entity_ids: List[List[str]],
                               rels_from_template: List[Tuple[str]]) -> List[Tuple[str]]:
        candidate_outputs = []
        log.debug(f"(find_candidate_answers)self.template_num: {self.template_num}")

        if self.template_num == 0 or self.template_num == 1:
            candidate_outputs = self.complex_question_with_number_solver(question, entity_ids)
            if not candidate_outputs:
                self.template_num = 7

        if self.template_num == 2 or self.template_num == 3:
            candidate_outputs = self.complex_question_with_qualifier_solver(question, entity_ids)
            if not candidate_outputs:
                self.template_num = 7

        if self.template_num == 4:
            candidate_outputs = self.questions_with_count_solver(question, entity_ids)

        if self.template_num == 5:
            candidate_outputs = self.maxmin_one_entity_solver(question, entity_ids[0][:self.entities_to_leave], rels_from_template)

        if self.template_num == 6:
            candidate_outputs = self.maxmin_two_entities_solver(question, entity_ids, rels_from_template)
            if not candidate_outputs:
                self.template_num = 5
                candidate_outputs = self.maxmin_one_entity_solver(question, entity_ids[0][:self.entities_to_leave])

        if self.template_num == 7:
            candidate_outputs = self.two_hop_solver(question, entity_ids, rels_from_template)

        log.debug("candidate_rels_and_answers:\n" + '\n'.join([str(output) for output in candidate_outputs]))

        return candidate_outputs

    def complex_question_with_number_solver(self, question: str, entity_ids: List[List[str]],
                                            rels_from_template: Optional[List[Tuple[str]]] = None) -> List[Tuple[str]]:
        question_tokens = nltk.word_tokenize(question)
        if rels_from_template is not None:
            top_rels = rels_from_template[0][:-1]
        else:
            ex_rels = []
            for entity in entity_ids[0][:self.entities_to_leave]:
                ex_rels += self.wiki_parser("rels", "forw", entity, type_of_rel="direct")
            ex_rels = list(set(ex_rels))
            scores = self.rel_ranker.rank_rels(question, ex_rels)
            top_rels = [score[0] for score in scores]
        log.debug(f"top scored rels: {top_rels}")
        year = extract_year(question_tokens, question)
        number = False
        if not year:
            number = extract_number(question_tokens, question)
        log.debug(f"year {year}, number {number}")

        candidate_outputs = []

        if year:
            candidate_outputs = self.find_relevant_subgraph_cqwn(entity_ids[0][:self.entities_to_leave],
                                                                 top_rels[:self.rels_to_leave], year)
            if not candidate_outputs:
                candidate_outputs = self.find_relevant_subgraph_cqwn(entity_ids[0][:self.entities_to_leave],
                                                                 ex_rels, year)

        if number:
            candidate_outputs = self.find_relevant_subgraph_cqwn(entity_ids[0][:self.entities_to_leave],
                                                                 top_rels[:self.rels_to_leave], number)
            if not candidate_outputs:
                candidate_outputs = self.find_relevant_subgraph_cqwn(entity_ids[0][:self.entities_to_leave],
                                                                 ex_rels, number)

        return candidate_outputs

    def complex_question_with_qualifier_solver(self, question: str, entity_ids: List[List[str]],
                                        rels_from_template: Optional[List[Tuple[str]]] = None) -> List[Tuple[str]]:
        if rels_from_template is not None:
            top_rels = rels_from_template[0][:-1]
        else:
            ex_rels = []
            for entity in entity_ids[0][:self.entities_to_leave]:
                ex_rels += self.wiki_parser("rels", "forw", entity, type_of_rel="direct")
            ex_rels = list(set(ex_rels))
            scores = self.rel_ranker.rank_rels(question, ex_rels)
            top_rels = [score[0] for score in scores]
        log.debug(f"top scored rels: {top_rels}")

        candidate_outputs = []
        if len(entity_ids) > 1:
            ent_combs = make_entity_combs(entity_ids)
            candidate_outputs = self.find_relevant_subgraph_cqwq(ent_combs, top_rels[:self.rels_to_leave])
        return candidate_outputs

    def questions_with_count_solver(self, question: str, entity_ids: List[List[str]],
                            rels_from_template: Optional[List[Tuple[str]]] = None) -> List[Tuple[str, str]]:
        candidate_outputs = []
        if rels_from_template is not None:
            top_rels = rels_from_template[0][:-1]
            directions = [rels_from_template[0][-1]]
        else:
            ex_rels = []
            for entity_id in entity_ids:
                for entity in entity_id[:self.entities_to_leave]:
                    ex_rels += self.wiki_parser("rels", "forw", entity, type_of_rel="direct")
                    ex_rels += self.wiki_parser("rels", "backw", entity, type_of_rel="direct")

            ex_rels = list(set(ex_rels))
            scores = self.rel_ranker.rank_rels(question, ex_rels)
            top_rels = [score[0] for score in scores]
            directions = ["forw", "backw"]
        log.debug(f"top scored rels: {top_rels}")
        
        for entity_id in entity_ids:
            for entity in entity_id[:self.entities_to_leave]:
                answers = []
                for rel in top_rels[:self.rels_to_leave]:
                    for direction in directions:
                        answers += self.wiki_parser("objects", direction, entity, rel, type_of_rel="direct")
                        if len(answers) > 0:
                            candidate_outputs.append((rel, str(len(answers))))

        return candidate_outputs

    def maxmin_one_entity_solver(self, question: str, entities_list: List[str],
                                 rels_from_template: Optional[List[Tuple[str]]] = None) -> List[Tuple[str, Any]]:
        if rels_from_template is not None:
            top_rels = rels_from_template[0][:-1]
        else:
            scores = self.rel_ranker.rank_rels(question, self.rank_list_0)
            top_rels = [score[0] for score in scores]
        log.debug(f"top scored rels: {top_rels}")
        ascending = asc_desc(question)
        candidate_outputs = self.find_relevant_subgraph_maxmin_one(entities_list, top_rels)
        reverse = True
        if ascending:
            reverse = False
        candidate_outputs = sorted(candidate_outputs, key=lambda x: x[2], reverse=reverse)
        candidate_outputs = [(output[0], output[1]) for output in candidate_outputs]
        if candidate_outputs:
            candidate_outputs = [candidate_outputs[0]]

        return candidate_outputs

    def maxmin_two_entities_solver(self, question: str, entity_ids: List[List[str]],
                          rels_from_template: Optional[List[Tuple[str]]] = None) -> List[Tuple[str, Any, Any]]:
        if rels_from_template is not None:
            top_rels_1 = rels_from_template[0][:-1]
            top_rels_2 = rels_from_template[1][:-1]
        else:
            ex_rels = []
            for entities_list in entity_ids:
                for entity in entities_list:
                    ex_rels += self.wiki_parser("rels", "backw", entity, type_of_rel="direct")

            ex_rels = list(set(ex_rels))
            scores_1 = self.rel_ranker.rank_rels(question, ex_rels)
            top_rels_1 = [score[0] for score in scores_1]
            log.debug(f"top scored first rels: {top_rels_1}")

            scores_2 = self.rel_ranker.rank_rels(question, self.rank_list_1)
            top_rels_2 = [score[0] for score in scores_2]
            log.debug(f"top scored second rels: {top_rels_2}")

        candidate_outputs = []

        if len(entity_ids) > 1:
            ent_combs = make_entity_combs(entity_ids)
            candidate_outputs = self.find_relevant_subgraph_maxmin_two(ent_combs, top_rels_1[:self.rels_to_leave],
                                                                       top_rels_2[:self.rels_to_leave])
            ascending = asc_desc(question)
            reverse = False
            if ascending:
                reverse = True
            candidate_outputs = sorted(candidate_outputs, key=lambda x: x[3], reverse=reverse)
        candidate_outputs = [(output[0], output[1], output[2]) for output in candidate_outputs]
        if candidate_outputs:
            candidate_outputs = [candidate_outputs[0]]

        return candidate_outputs

    def two_hop_solver(self, question: str,
                       entity_ids: List[List[str]],
                       rels_from_template: Optional[List[Tuple[str]]] = None) -> List[Tuple[str]]:
        candidate_outputs = []

        if len(entity_ids) == 1:
            if rels_from_template is not None:
                candidate_outputs = self.from_template_one_ent(entity_ids, rels_from_template)
            else:
                candidate_outputs = self.two_hop_one_ent(question, entity_ids[0])

        if len(entity_ids) >= 2:
            entity_ids_curr = [entity_ids[0], entity_ids[1]]
            ent_combs = make_entity_combs(entity_ids_curr)
            if rels_from_template is not None:
                candidate_outputs = self.from_template_two_ent(ent_combs, rels_from_template)
            else:
                candidate_outputs = self.two_hop_two_ent(question, ent_combs)
                if not candidate_outputs:
                    if len(entity_ids) == 3:
                        entity_ids_curr = [entity_ids[0], entity_ids[2]]
                        ent_combs = make_entity_combs(entity_ids_curr)
                        candidate_outputs = self.two_hop_two_ent(question, ent_combs)
                    else:
                        candidate_outputs = self.two_hop_one_ent(question, entity_ids[1])

        return candidate_outputs

    def find_relevant_subgraph_cqwn(self, entities_list: List[str], rels: List[str], num: str) -> List[Tuple[str]]:
        candidate_outputs = []

        for entity in entities_list:
            for rel in rels:
                objects_1 = self.wiki_parser("objects", "forw", entity, rel, type_of_rel=None)
                for obj in objects_1:
                    if self.template_num == 0:
                        answers = self.wiki_parser("objects", "forw", obj, rel, type_of_rel="statement")
                        second_rels = self.wiki_parser("rels", "forw", obj, type_of_rel="qualifier", filter_obj=num)
                        if len(second_rels) > 0 and len(answers) > 0:
                            for second_rel in second_rels:
                                for ans in answers:
                                    candidate_outputs.append((rel, second_rel, ans))
                    if self.template_num == 1:
                        answer_triplets = self.wiki_parser("triplets", "forw", obj, type_of_rel="qualifier")
                        second_rels = self.wiki_parser("rels", "forw", obj, rel,
                                                       type_of_rel="statement", filter_obj=num)
                        if len(second_rels) > 0 and len(answer_triplets) > 0:
                            for ans in answer_triplets:
                                candidate_outputs.append((rel, ans[1], ans[2]))

        return candidate_outputs

    def find_relevant_subgraph_cqwq(self, ent_combs: List[Tuple[str]], rels: List[str]) -> List[Tuple[str]]:
        candidate_outputs = []
        for ent_comb in ent_combs:
            for rel in rels:
                objects_1 = self.wiki_parser("objects", "forw", ent_comb[0], rel, type_of_rel=None)
                for obj in objects_1:
                    if self.template_num == 2:
                        answer_triplets = self.wiki_parser("triplets", "forw", obj, type_of_rel="qualifier")
                        second_rels = self.wiki_parser("rels", "backw", ent_comb[1], rel, obj, type_of_rel="statement")
                        if len(second_rels) > 0 and len(answer_triplets) > 0:
                            for ans in answer_triplets:
                                candidate_outputs.append((rel, ans[1], ans[2]))
                    if self.template_num == 3:
                        answers = self.wiki_parser("objects", "forw", obj, rel, type_of_rel="statement")
                        second_rels = self.wiki_parser("rels", "backw", ent_comb[1], rel=None,
                                                       obj=obj, type_of_rel="qualifier")
                        if len(second_rels) > 0 and len(answers) > 0:
                            for second_rel in second_rels:
                                for ans in answers:
                                    candidate_outputs.append((rel, second_rel, ans))

        return candidate_outputs

    def find_relevant_subgraph_maxmin_one(self, entities_list: List[str], rels: List[str]) -> List[Tuple[str]]:
        candidate_answers = []

        for entity in entities_list:
            objects_1 = self.wiki_parser("objects", "backw", entity, "P31", type_of_rel="direct")
            for rel in rels:
                candidate_answers = []
                for obj in objects_1:
                    objects_2 = self.wiki_parser("objects", "forw", obj, rel, type_of_rel="direct",
                                                 filter_obj="http://www.w3.org/2001/XMLSchema#decimal")
                    if len(objects_2) > 0:
                        number = re.search(r'["]([^"]*)["]*', objects_2[0]).group(1)
                        candidate_answers.append((rel, obj, float(number)))

                if len(candidate_answers) > 0:
                    return candidate_answers

        return candidate_answers

    def find_relevant_subgraph_maxmin_two(self, ent_combs: List[Tuple[str]],
                                          rels_1: List[str],
                                          rels_2: List[str]) -> List[Tuple[str]]:
        candidate_answers = []

        for ent_comb in ent_combs:
            objects_1 = self.wiki_parser("objects", "backw", ent_comb[0], "P31", type_of_rel="direct")
            for rel_1 in rels_1:
                objects_2 = self.wiki_parser("objects", "backw", ent_comb[1], rel_1, type_of_rel="direct")
                objects_intersect = list(set(objects_1) & set(objects_2))
                for rel_2 in rels_2:
                    candidate_answers = []
                    for obj in objects_intersect:
                        objects_3 = self.wiki_parser("objects", "forw", obj, rel_2, type_of_rel="direct",
                                                     filter_obj="http://www.w3.org/2001/XMLSchema#decimal")
                        if len(objects_3) > 0:
                            number = re.search(r'["]([^"]*)["]*', objects_3[0]).group(1)
                            candidate_answers.append((rel_1, rel_2, obj, float(number)))

                    if len(candidate_answers) > 0:
                        return candidate_answers

        return candidate_answers

    def from_template_one_ent(self, entity_ids: List[List[str]],
                              rels_from_template: List[Tuple[str]]) -> List[Tuple[str]]:
        candidate_outputs = []
        if len(rels_from_template) == 1:
            relations = rels_from_template[0][:-1]
            direction = rels_from_template[0][-1]
            for entity in entity_ids[0]:
                for relation in relations:
                    objects = self.wiki_parser("objects", direction, entity, relation, type_of_rel="direct")
                    if objects:
                        candidate_outputs.append((relation, objects[0]))
                        return candidate_outputs

        if len(rels_from_template) == 2:
            relations_1 = rels_from_template[0][:-1]
            direction_1 = rels_from_template[0][-1]
            relations_2 = rels_from_template[1][:-1]
            direction_2 = rels_from_template[1][-1]
            for entity in entity_ids[0]:
                for relation_1 in relations_1:
                    objects_1 = self.wiki_parser("objects", direction_1, entity, relation_1, type_of_rel="direct")
                    for object_1 in objects_1:
                        for relation_2 in relations_2:
                            objects_2 = self.wiki_parser("objects", direction_2, object_1, relation_2, type_of_rel="direct")
                            if objects_2:
                                for object_2 in objects_2:
                                    candidate_outputs.append((relation_1, relation_2, object_2))
                                    return candidate_outputs

        return candidate_outputs

    def from_template_two_ent(self, ent_combs: List[Tuple[str]],
                              rels_from_template: List[Tuple[str]]) -> List[Tuple[str]]:
        candidate_outputs = []
        if len(rels_from_template) == 1:
            relations = rels_from_template[0][:-1]
            direction = rels_from_template[0][-1]
            for ent_comb in ent_combs:
                for relation in relations:
                    objects_1 = self.wiki_parser("objects", direction, ent_comb[1], relation, type_of_rel="direct")
                    if objects_1:
                        for object_1 in objects_1:
                            objects_2 = self.wiki_parser("objects", direction, object_1, "P31", obj=ent_comb[0],
                                                         type_of_rel="direct")
                            if objects_2:
                                candidate_outputs.append((relation, objects_2[0]))
                                return candidate_outputs

        if len(rels_from_template) == 2:
            relations_1 = rels_from_template[0][:-1]
            direction_1 = rels_from_template[0][-1]
            relations_2 = rels_from_template[1][:-1]
            direction_2 = rels_from_template[1][-1]
            for ent_comb in ent_combs:
                for relation_1 in relations_1:
                    for relation_2 in relations_2:
                        objects_1 = self.wiki_parser("objects", direction_1, ent_comb[0], relation_1, type_of_rel="direct")
                        objects_2 = self.wiki_parser("objects", direction_2, ent_comb[1], relation_2, type_of_rel="direct")
                        objects_intersect = list(set(objects_1) & set(objects_2))
                        if objects_intersect:
                            return [(relation_1, relation_2, objects_intersect[0])]

        return candidate_outputs

    def two_hop_two_ent(self, question: str, ent_combs: List[Tuple[str]]) -> List[Tuple[str]]:
        candidate_outputs = []
        for ent_comb in ent_combs:
            ex_rels = []
            ex_rels += self.wiki_parser("rels", "forw", ent_comb[1], type_of_rel="direct")
            ex_rels += self.wiki_parser("rels", "backw", ent_comb[1], type_of_rel="direct")

            ex_rels = list(set(ex_rels))
            scores = self.rel_ranker.rank_rels(question, ex_rels)
            top_rels = [score[0] for score in scores]

            for rel in top_rels:
                objects_1 = self.wiki_parser("objects", "forw", ent_comb[1], rel, type_of_rel="direct")
                objects_1 += self.wiki_parser("objects", "backw", ent_comb[1], rel, type_of_rel="direct")
                for object_1 in objects_1:
                    objects_2 = self.wiki_parser("rels", "forw", object_1, obj=ent_comb[0],
                                                 type_of_rel="direct")
                    if objects_2:
                        for object_2 in objects_2:
                            if object_2 != "P31":
                                candidate_outputs.append((rel, object_2, object_1))
                            else:
                                candidate_outputs.append((rel, object_1))
                        log.debug(f"candidate_outputs {rel}, {object_1}, {objects_2}")
                        return candidate_outputs

        return candidate_outputs

    def two_hop_cqwn(self, entities_list: List[str], rels: List[str], num: str) -> List[Tuple[str]]:
        candidate_outputs = []
        for entity in entities_list:
            for rel in rels:
                answers = self.wiki_parser("objects", "forw", entity, rel, type_of_rel="direct")
                for ans in answers:
                    second_rels = self.wiki_parser("rels", "forw", ans, type_of_rel="direct", filter_obj=num)
                    if len(second_rels) > 0:
                        for second_rel in second_rels:
                            candidate_outputs.append((rel, second_rel, ans))
                        return candidate_outputs
        return candidate_outputs

    def two_hop_one_ent(self, question: str, entities_list: List[str]) -> List[Tuple[str]]:
        log.debug(f"two hop one entity {entities_list}")
        candidate_outputs = []
        question_tokens = nltk.word_tokenize(question)
        year = extract_year(question_tokens, question)
        number = False
        if not year:
            number = extract_number(question_tokens, question)
        log.debug(f"year {year}, number {number}")

        ex_rels = []
        for entity in entities_list[:self.entities_to_leave]:
            ex_rels += self.wiki_parser("rels", "forw", entity, type_of_rel="direct")
            ex_rels += self.wiki_parser("rels", "backw", entity, type_of_rel="direct")

        ex_rels = list(set(ex_rels))
        scores = self.rel_ranker.rank_rels(question, ex_rels)
        top_rels = [score[0] for score in scores]
        log.debug(f"top scored rels: {top_rels}")

        if year:
            candidate_outputs = self.two_hop_cqwn(entities_list[:self.entities_to_leave], top_rels, year[:3])
            if candidate_outputs:
                return candidate_outputs
        elif number:
            candidate_outputs = self.two_hop_cqwn(entities_list[:self.entities_to_leave], top_rels, number)
            if candidate_outputs:
                return candidate_outputs
        else:
            ex_rels_2 = []
            for entity in entities_list[:self.entities_to_leave]:
                for rel in top_rels[:self.rels_to_leave_2hop]:
                    if rel != "P31":
                        objects_mid = self.wiki_parser("objects", "forw", entity, rel, type_of_rel="direct")
                        objects_mid += self.wiki_parser("objects", "backw", entity, rel, type_of_rel="direct")
                        if len(objects_mid) < 15:
                            for obj in objects_mid:
                                ex_rels_2 += self.wiki_parser("rels", "forw", obj, type_of_rel="direct")

            ex_rels_2 = list(set(ex_rels_2))
            scores_2 = self.rel_ranker.rank_rels(question, ex_rels_2)
            top_rels_2 = [score[0] for score in scores_2]
            log.debug(f"top scored second rels: {top_rels_2}")

            for entity in entities_list[:self.entities_to_leave]:
                for rel in top_rels[:self.rels_to_leave_2hop]:
                    if rel != "P31":
                        objects = self.wiki_parser("objects", "forw", entity, rel, type_of_rel="direct")
                        objects += self.wiki_parser("objects", "backw", entity, rel, type_of_rel="direct")
                        if objects:
                            candidate_outputs.append((rel, objects[0]))

            for entity in entities_list[:self.entities_to_leave]:
                for rel_1 in top_rels[:self.rels_to_leave_2hop]:
                    if rel_1 != "P31":
                        objects_mid = self.wiki_parser("objects", "forw", entity, rel_1, type_of_rel="direct")
                        objects_mid += self.wiki_parser("objects", "backw", entity, rel_1, type_of_rel="direct")
                        if objects_mid and len(objects_mid) < 15:
                            for obj in objects_mid:
                                for rel_2 in top_rels_2[:self.rels_to_leave_2hop]:
                                    if rel_2 != "P31" and rel_1 != rel_2:
                                        objects = self.wiki_parser("objects", "forw", obj, rel_2, type_of_rel="direct")
                                        if objects:
                                            candidate_outputs.append((rel_1, rel_2, objects[0]))
        return candidate_outputs
