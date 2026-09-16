"""Server-authored application help; never substitute this for enterprise evidence."""
import re
import unicodedata
from .config import settings

HELP = '我是知序，可以查询你有权访问的项目负责人、当前状态和历史文档，并提供来源。你可以问“PRJ-001 的负责人是谁？”或提供项目名称和要查的内容。'


def application_reply(message):
    # Full-message matching keeps mixed business requests on the evidence path.
    text = unicodedata.normalize('NFKC', message).strip().lower()
    text = re.sub(r'[\s?？!！。]+$', '', text)
    text = re.sub(r'^(?:请问|请告诉我|我想知道)[,，\s]*', '', text)
    if re.fullmatch(r'你(?:是|用的(?:是)?|使用的(?:是)?)(?:什么|哪个)(?:大语言|大)?模型(?:呀|啊|呢|吗)?', text) or text in {
        '你是什么模型', '你用的模型是什么', '你是deepseek吗', '你是 deepseek 吗', 'what model are you',
    }:
        return f'我是知序项目知识助手，当前配置的回答模型是 {settings.model_name}。项目资料的语义检索由独立的 Embedding 模型完成。'
    if text in {'你好', '您好', '嗨', 'hello', 'hi', '你是谁', '你能做什么', '你可以做什么', '怎么用', '如何使用', '使用帮助', '帮助', 'help'}:
        return HELP
    return None


def notice(text):
    return {'status': 'answered', 'kind': 'application', 'answer': text,
            'facts': [], 'evidence': [], 'warnings': [], 'clarification': None}
