from dataclasses import dataclass, field

from Core.PromptSection import PromptSection


@dataclass
class PromptDocument:
    system_sections: list[PromptSection] = field(default_factory=list)
    conversation_sections: list[PromptSection] = field(default_factory=list)

    def add_system(self, section: PromptSection):
        self.system_sections.append(section)

    def add_conversation(self, section: PromptSection):
        self.conversation_sections.append(section)

    def extend_system(self, sections):
        self.system_sections.extend(list(sections or []))

    def extend_conversation(self, sections):
        self.conversation_sections.extend(list(sections or []))

    def all_sections(self):
        return [*self.system_sections, *self.conversation_sections]
