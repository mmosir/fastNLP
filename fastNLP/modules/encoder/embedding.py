__all__ = [
    "Embedding"
]
import torch.nn as nn
from ..utils import get_embeddings
from .lstm import LSTM
from ... import Vocabulary
from abc import abstractmethod
import torch
from ...io import EmbedLoader
import torch.nn.functional as F
import os
from ._elmo import _ElmoModel
from ...io.file_utils import cached_path, _get_base_url
from ._bert import _WordBertModel
from typing import List

from ... import DataSet, Batch, SequentialSampler
from ...core.utils import _move_model_to_device, _get_model_device


class Embedding(nn.Module):
    """
    别名：:class:`fastNLP.modules.Embedding`   :class:`fastNLP.modules.encoder.embedding.Embedding`

    Embedding组件. 可以通过self.num_embeddings获取词表大小; self.embedding_dim获取embedding的维度"""
    
    def __init__(self, init_embed, dropout=0.0):
        """

        :param tuple(int,int),torch.FloatTensor,nn.Embedding,numpy.ndarray init_embed: Embedding的大小(传入tuple(int, int),
            第一个int为vocab_zie, 第二个int为embed_dim); 如果为Tensor, Embedding, ndarray等则直接使用该值初始化Embedding;
            也可以传入TokenEmbedding对象
        :param float dropout: 对Embedding的输出的dropout。
        """
        super(Embedding, self).__init__()

        self.embed = get_embeddings(init_embed)
        
        self.dropout = nn.Dropout(dropout)
        if not isinstance(self.embed, TokenEmbedding):
            self._embed_size = self.embed.weight.size(1)
        else:
            self._embed_size = self.embed.embed_size
    
    def forward(self, x):
        """
        :param torch.LongTensor x: [batch, seq_len]
        :return: torch.Tensor : [batch, seq_len, embed_dim]
        """
        x = self.embed(x)
        return self.dropout(x)

    @property
    def num_embedding(self)->int:
        return len(self)

    def __len__(self):
        return len(self.embed)

    @property
    def embed_size(self) -> int:
        return self._embed_size

    @property
    def embedding_dim(self) -> int:
        return self._embed_size

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许
        :return:
        """
        if not isinstance(self.embed, TokenEmbedding):
            return self.embed.weight.requires_grad
        else:
            return self.embed.requires_grad

    @requires_grad.setter
    def requires_grad(self, value):
        if not isinstance(self.embed, TokenEmbedding):
            self.embed.weight.requires_grad = value
        else:
            self.embed.requires_grad = value

    @property
    def size(self):
        if isinstance(self.embed, TokenEmbedding):
            return torch.Size(self.embed._word_vocab, self.embed.embed_size)
        else:
            return self.embed.weight.size()


class TokenEmbedding(nn.Module):
    def __init__(self, vocab):
        super(TokenEmbedding, self).__init__()
        assert vocab.padding_idx is not None, "You vocabulary must have padding."
        self._word_vocab = vocab
        self._word_pad_index = vocab.padding_idx

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许
        :return:
        """
        requires_grads = set([param.requires_grad for param in self.parameters()])
        if len(requires_grads) == 1:
            return requires_grads.pop()
        else:
            return None

    @requires_grad.setter
    def requires_grad(self, value):
        for param in self.parameters():
            param.requires_grad = value

    def __len__(self):
        return len(self._word_vocab)

    @property
    def embed_size(self) -> int:
        return self._embed_size

    def get_word_vocab(self):
        """
        返回embedding的词典。

        :return: Vocabulary
        """
        return self._word_vocab

    @property
    def size(self):
        return torch.Size(self.embed._word_vocab, self._embed_size)


class StaticEmbedding(TokenEmbedding):
    """
    别名：:class:`fastNLP.modules.StaticEmbedding`   :class:`fastNLP.modules.encoder.embedding.StaticEmbedding`

    StaticEmbedding组件. 给定embedding的名称，根据vocab从embedding中抽取相应的数据。该Embedding可以就按照正常的embedding使用了

    Example::


    :param vocab: Vocabulary. 若该项为None则会读取所有的embedding。
    :param model_dir_or_name: 可以有两种方式调用预训练好的static embedding：第一种是传入embedding的文件名，第二种是传入embedding
        的名称。目前支持的embedding包括{`en` 或者 `en-glove-840b-300` : glove.840B.300d, `en-glove-6b-50` : glove.6B.50d,
        `en-word2vec-300` : GoogleNews-vectors-negative300}。第二种情况将自动查看缓存中是否存在该模型，没有的话将自动下载。
    :param requires_grad: 是否需要gradient

    """

    def __init__(self, vocab: Vocabulary, model_dir_or_name: str='en', requires_grad: bool=False):
        super(StaticEmbedding, self).__init__(vocab)

        # 优先定义需要下载的static embedding有哪些。这里估计需要自己搞一个server，
        PRETRAIN_URL = _get_base_url('static')
        PRETRAIN_STATIC_FILES = {
            'en': 'glove.840B.300d-cc1ad5e1.tar.gz',
            'en-glove-840b-300': 'glove.840B.300d-cc1ad5e1.tar.gz',
            'en-glove-6b-50': "glove.6B.50d-a6028c70.tar.gz",
            'en-word2vec-300': "GoogleNews-vectors-negative300-be166d9d.tar.gz",
            'en-fasttext': "cc.en.300.vec-d53187b2.gz",
            'cn': "tencent_cn-dab24577.tar.gz",
            'cn-fasttext': "cc.zh.300.vec-d68a9bcf.gz",
        }

        # 得到cache_path
        if model_dir_or_name.lower() in PRETRAIN_STATIC_FILES:
            model_name = PRETRAIN_STATIC_FILES[model_dir_or_name]
            model_url = PRETRAIN_URL + model_name
            model_path = cached_path(model_url)
            # 检查是否存在
        elif os.path.isfile(model_dir_or_name):
            model_path = model_dir_or_name
        else:
            raise ValueError(f"Cannot recognize {model_dir_or_name}.")

        # 读取embedding
        embedding = EmbedLoader.load_with_vocab(model_path, vocab=vocab)
        embedding = torch.tensor(embedding)
        self.embedding = nn.Embedding(num_embeddings=embedding.shape[0], embedding_dim=embedding.shape[1],
                                      padding_idx=vocab.padding_idx,
                                      max_norm=None, norm_type=2, scale_grad_by_freq=False,
                                      sparse=False, _weight=embedding)
        self._embed_size = self.embedding.weight.size(1)
        self.requires_grad = requires_grad

    def forward(self, words):
        """
        传入words的index

        :param words: torch.LongTensor, [batch_size, max_len]
        :return: torch.FloatTensor, [batch_size, max_len, embed_size]
        """
        return self.embedding(words)


class ContextualEmbedding(TokenEmbedding):
    def __init__(self, vocab: Vocabulary):
        super(ContextualEmbedding, self).__init__(vocab)

    def add_sentence_cache(self, *datasets, batch_size=32, device='cpu', delete_weights: bool=True):
        """
        由于动态embedding生成比较耗时，所以可以把每句话embedding缓存下来，这样就不需要每次都运行生成过程。

        Example::

            >>>


        :param datasets: DataSet对象
        :param batch_size: int, 生成cache的sentence表示时使用的batch的大小
        :param device: 参考 :class::fastNLP.Trainer 的device
        :param delete_weights: 似乎在生成了cache之后删除权重，在不需要finetune动态模型的情况下，删除权重会大量减少内存占用。
        :return:
        """
        for index, dataset in enumerate(datasets):
            try:
                assert isinstance(dataset, DataSet), "Only fastNLP.DataSet object is allowed."
                assert 'words' in dataset.get_input_name(), "`words` field has to be set as input."
            except Exception as e:
                print(f"Exception happens at {index} dataset.")
                raise e

        sent_embeds = {}
        _move_model_to_device(self, device=device)
        device = _get_model_device(self)
        pad_index = self._word_vocab.padding_idx
        print("Start to calculate sentence representations.")
        with torch.no_grad():
            for index, dataset in enumerate(datasets):
                try:
                    batch = Batch(dataset, batch_size=batch_size, sampler=SequentialSampler(), prefetch=False)
                    for batch_x, batch_y in batch:
                        words = batch_x['words'].to(device)
                        words_list = words.tolist()
                        seq_len = words.ne(pad_index).sum(dim=-1)
                        max_len = words.size(1)
                        # 因为有些情况可能包含CLS, SEP, 从后面往前计算比较安全。
                        seq_len_from_behind =(max_len - seq_len).tolist()
                        word_embeds = self(words).detach().cpu().numpy()
                        for b in range(words.size(0)):
                            length = seq_len_from_behind[b]
                            if length==0:
                                sent_embeds[tuple(words_list[b][:seq_len[b]])] = word_embeds[b]
                            else:
                                sent_embeds[tuple(words_list[b][:seq_len[b]])] = word_embeds[b, :-length]
                except Exception as e:
                    print(f"Exception happens at {index} dataset.")
                    raise e
        print("Finish calculating sentence representations.")
        self.sent_embeds = sent_embeds
        if delete_weights:
            self._delete_model_weights()

    def _get_sent_reprs(self, words):
        """
        获取sentence的表示，如果有缓存，则返回缓存的值; 没有缓存则返回None

        :param words: torch.LongTensor
        :return:
        """
        if hasattr(self, 'sent_embeds'):
            words_list = words.tolist()
            seq_len = words.ne(self._word_pad_index).sum(dim=-1)
            _embeds = []
            for b in range(len(words)):
                words_i = tuple(words_list[b][:seq_len[b]])
                embed = self.sent_embeds[words_i]
                _embeds.append(embed)
            max_sent_len = max(map(len, _embeds))
            embeds = words.new_zeros(len(_embeds), max_sent_len, self.embed_size, dtype=torch.float,
                                     device=words.device)
            for i, embed in enumerate(_embeds):
                embeds[i, :len(embed)] = torch.FloatTensor(embed).to(words.device)
            return embeds
        return None

    @abstractmethod
    def _delete_model_weights(self):
        """删除计算表示的模型以节省资源"""
        raise NotImplementedError

    def remove_sentence_cache(self):
        """
        删除缓存的句子表示. 删除之后如果模型权重没有被删除，将开始使用动态计算权重。

        :return:
        """
        del self.sent_embeds


class ElmoEmbedding(ContextualEmbedding):
    """
    别名：:class:`fastNLP.modules.ElmoEmbedding`   :class:`fastNLP.modules.encoder.embedding.ElmoEmbedding`

    使用ELMo的embedding。初始化之后，只需要传入words就可以得到对应的embedding。
    我们提供的ELMo预训练模型来自 https://github.com/HIT-SCIR/ELMoForManyLangs

    Example::

        >>>
        >>>

    :param vocab: 词表
    :param model_dir_or_name: 可以有两种方式调用预训练好的ELMo embedding：第一种是传入ELMo权重的文件名，第二种是传入ELMo版本的名称，
        目前支持的ELMo包括{`en` : 英文版本的ELMo, `cn` : 中文版本的ELMo,}。第二种情况将自动查看缓存中是否存在该模型，没有的话将自动下载
    :param layers: str, 指定返回的层数, 以,隔开不同的层。如果要返回第二层的结果'2', 返回后两层的结果'1,2'。不同的层的结果
        按照这个顺序concat起来。默认为'2'。
    :param requires_grad: bool, 该层是否需要gradient. 默认为False
    :param cache_word_reprs: 可以选择对word的表示进行cache; 设置为True的话，将在初始化的时候为每个word生成对应的embedding，
        并删除character encoder，之后将直接使用cache的embedding。默认为False。
    """
    def __init__(self, vocab: Vocabulary, model_dir_or_name: str='en',
                 layers: str='2', requires_grad: bool=False, cache_word_reprs: bool=False):
        super(ElmoEmbedding, self).__init__(vocab)
        layers = list(map(int, layers.split(',')))
        assert len(layers) > 0, "Must choose one output"
        for layer in layers:
            assert 0 <= layer <= 2, "Layer index should be in range [0, 2]."
        self.layers = layers

        # 根据model_dir_or_name检查是否存在并下载
        PRETRAIN_URL = _get_base_url('elmo')
        PRETRAINED_ELMO_MODEL_DIR = {'en': 'elmo_en-d39843fe.tar.gz',
                                     'cn': 'elmo_cn-5e9b34e2.tar.gz'}

        if model_dir_or_name.lower() in PRETRAINED_ELMO_MODEL_DIR:
            model_name = PRETRAINED_ELMO_MODEL_DIR[model_dir_or_name]
            model_url = PRETRAIN_URL + model_name
            model_dir = cached_path(model_url)
            # 检查是否存在
        elif os.path.isdir(model_dir_or_name):
            model_dir = model_dir_or_name
        else:
            raise ValueError(f"Cannot recognize {model_dir_or_name}.")
        self.model = _ElmoModel(model_dir, vocab, cache_word_reprs=cache_word_reprs)
        self.requires_grad = requires_grad
        self._embed_size = len(self.layers) * self.model.config['encoder']['projection_dim'] * 2

    def forward(self, words: torch.LongTensor):
        """
        计算words的elmo embedding表示。根据elmo文章中介绍的ELMO实际上是有2L+1层结果，但是为了让结果比较容易拆分，token的
            被重复了一次，使得实际上layer=0的结果是[token_embedding;token_embedding], 而layer=1的结果是[forward_hiddens;
            backward_hiddens].

        :param words: batch_size x max_len
        :return: torch.FloatTensor. batch_size x max_len x (512*len(self.layers))
        """
        outputs = self._get_sent_reprs(words)
        if outputs is not None:
            return outputs
        outputs = self.model(words)
        if len(self.layers) == 1:
            outputs = outputs[self.layers[0]]
        else:
            outputs = torch.cat([*outputs[self.layers]], dim=-1)

        return outputs

    def _delete_model_weights(self):
        del self.layers, self.model

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许

        :return:
        """
        requires_grads = set([param.requires_grad for name, param in self.named_parameters()
                             if 'words_to_chars_embedding' not in name])
        if len(requires_grads) == 1:
            return requires_grads.pop()
        else:
            return None

    @requires_grad.setter
    def requires_grad(self, value):
        for name, param in self.named_parameters():
            if 'words_to_chars_embedding' in name: # 这个不能加入到requires_grad中
                pass
            param.requires_grad = value


class BertEmbedding(ContextualEmbedding):
    """
    别名：:class:`fastNLP.modules.BertEmbedding`   :class:`fastNLP.modules.encoder.embedding.BertEmbedding`

    使用BERT对words进行encode的Embedding。

    Example::

        >>>


    :param fastNLP.Vocabulary vocab: 词表
    :param str model_dir_or_name: 模型所在目录或者模型的名称。默认值为``en-base-uncased``
    :param str layers:最终结果中的表示。以','隔开层数，可以以负数去索引倒数几层
    :param str pool_method: 因为在bert中，每个word会被表示为多个word pieces, 当获取一个word的表示的时候，怎样从它的word pieces
        中计算得到他对应的表示。支持``last``, ``first``, ``avg``, ``max``.
    :param bool include_cls_sep: bool，在bert计算句子的表示的时候，需要在前面加上[CLS]和[SEP], 是否在结果中保留这两个内容。 这样
        会使得word embedding的结果比输入的结果长两个token。在使用 :class::StackEmbedding 可能会遇到问题。
    :param bool requires_grad: 是否需要gradient。
    """
    def __init__(self, vocab: Vocabulary, model_dir_or_name: str='en-base-uncased', layers: str='-1',
                 pool_method: str='first', include_cls_sep: bool=False, requires_grad: bool=False):
        super(BertEmbedding, self).__init__(vocab)
        # 根据model_dir_or_name检查是否存在并下载
        PRETRAIN_URL = _get_base_url('bert')
        PRETRAINED_BERT_MODEL_DIR = {'en': 'bert-base-cased-f89bfe08.zip',
                                     'en-base-uncased': 'bert-base-uncased-3413b23c.zip',
                                     'en-base-cased': 'bert-base-cased-f89bfe08.zip',
                                     'en-large-uncased': 'bert-large-uncased-20939f45.zip',
                                     'en-large-cased': 'bert-large-cased-e0cf90fc.zip',

                                     'cn': 'bert-base-chinese-29d0a84a.zip',
                                     'cn-base': 'bert-base-chinese-29d0a84a.zip',

                                     'multilingual': 'bert-base-multilingual-cased-1bd364ee.zip',
                                     'multilingual-base-uncased': 'bert-base-multilingual-uncased-f8730fe4.zip',
                                     'multilingual-base-cased': 'bert-base-multilingual-cased-1bd364ee.zip',
                                     }

        if model_dir_or_name.lower() in PRETRAINED_BERT_MODEL_DIR:
            model_name = PRETRAINED_BERT_MODEL_DIR[model_dir_or_name]
            model_url = PRETRAIN_URL + model_name
            model_dir = cached_path(model_url)
            # 检查是否存在
        elif os.path.isdir(model_dir_or_name):
            model_dir = model_dir_or_name
        else:
            raise ValueError(f"Cannot recognize {model_dir_or_name}.")

        self.model = _WordBertModel(model_dir=model_dir, vocab=vocab, layers=layers,
                                    pool_method=pool_method, include_cls_sep=include_cls_sep)

        self.requires_grad = requires_grad
        self._embed_size = len(self.model.layers)*self.model.encoder.hidden_size

    def _delete_model_weights(self):
        del self.model

    def forward(self, words):
        """
        计算words的bert embedding表示。计算之前会在每句话的开始增加[CLS]在结束增加[SEP], 并根据include_cls_sep判断要不要
            删除这两个token的表示。

        :param words: batch_size x max_len
        :return: torch.FloatTensor. batch_size x max_len x (768*len(self.layers))
        """
        outputs = self._get_sent_reprs(words)
        if outputs is not None:
            return outputs
        outputs = self.model(words)
        outputs = torch.cat([*outputs], dim=-1)

        return outputs

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许
        :return:
        """
        requires_grads = set([param.requires_grad for name, param in self.named_parameters()
                             if 'word_pieces_lengths' not in name])
        if len(requires_grads) == 1:
            return requires_grads.pop()
        else:
            return None

    @requires_grad.setter
    def requires_grad(self, value):
        for name, param in self.named_parameters():
            if 'word_pieces_lengths' in name:  # 这个不能加入到requires_grad中
                pass
            param.requires_grad = value


def _construct_char_vocab_from_vocab(vocab:Vocabulary, min_freq:int=1):
    """
    给定一个word的vocabulary生成character的vocabulary.

    :param vocab: 从vocab
    :param min_freq:
    :return:
    """
    char_vocab = Vocabulary(min_freq=min_freq)
    for word, index in vocab:
        char_vocab.add_word_lst(list(word))
    return char_vocab


class CNNCharEmbedding(TokenEmbedding):
    """
    别名：:class:`fastNLP.modules.CNNCharEmbedding`   :class:`fastNLP.modules.encoder.embedding.CNNCharEmbedding`

    使用CNN生成character embedding。CNN的结果为, CNN(x) -> activation(x) -> pool -> fc. 不同的kernel大小的fitler结果是
        concat起来的。

    Example::

        >>>


    :param vocab: 词表
    :param embed_size: 该word embedding的大小，默认值为50.
    :param char_emb_size: character的embed的大小。character是从vocab中生成的。默认值为50.
    :param filter_nums: filter的数量. 长度需要和kernels一致。默认值为[40, 30, 20].
    :param kernel_sizes: kernel的大小. 默认值为[5, 3, 1].
    :param pool_method: character的表示在合成一个表示时所使用的pool方法，支持'avg', 'max'.
    :param activation: CNN之后使用的激活方法，支持'relu', 'sigmoid', 'tanh' 或者自定义函数.
    :param min_char_freq: character的最少出现次数。默认值为2.
    """
    def __init__(self, vocab: Vocabulary, embed_size: int=50, char_emb_size: int=50,
                 filter_nums: List[int]=(40, 30, 20), kernel_sizes: List[int]=(5, 3, 1), pool_method: str='max',
                 activation='relu', min_char_freq: int=2):
        super(CNNCharEmbedding, self).__init__(vocab)

        for kernel in kernel_sizes:
            assert kernel % 2 == 1, "Only odd kernel is allowed."

        assert pool_method in ('max', 'avg')
        self.pool_method = pool_method
        # activation function
        if isinstance(activation, str):
            if activation.lower() == 'relu':
                self.activation = F.relu
            elif activation.lower() == 'sigmoid':
                self.activation = F.sigmoid
            elif activation.lower() == 'tanh':
                self.activation = F.tanh
        elif activation is None:
            self.activation = lambda x: x
        elif callable(activation):
            self.activation = activation
        else:
            raise Exception(
                "Undefined activation function: choose from: [relu, tanh, sigmoid, or a callable function]")

        print("Start constructing character vocabulary.")
        # 建立char的词表
        self.char_vocab = _construct_char_vocab_from_vocab(vocab, min_freq=min_char_freq)
        self.char_pad_index = self.char_vocab.padding_idx
        print(f"In total, there are {len(self.char_vocab)} distinct characters.")
        # 对vocab进行index
        self.max_word_len = max(map(lambda x: len(x[0]), vocab))
        self.words_to_chars_embedding = nn.Parameter(torch.full((len(vocab), self.max_word_len),
                                                                fill_value=self.char_pad_index, dtype=torch.long),
                                                     requires_grad=False)
        self.word_lengths = nn.Parameter(torch.zeros(len(vocab)).long(), requires_grad=False)
        for word, index in vocab:
            # if index!=vocab.padding_idx:  # 如果是pad的话，直接就为pad_value了。 修改为不区分pad, 这样所有的<pad>也是同一个embed
            self.words_to_chars_embedding[index, :len(word)] = \
                torch.LongTensor([self.char_vocab.to_index(c) for c in word])
            self.word_lengths[index] = len(word)
        self.char_embedding = nn.Embedding(len(self.char_vocab), char_emb_size)

        self.convs = nn.ModuleList([nn.Conv1d(
            char_emb_size, filter_nums[i], kernel_size=kernel_sizes[i], bias=True, padding=kernel_sizes[i] // 2)
            for i in range(len(kernel_sizes))])
        self._embed_size = embed_size
        self.fc = nn.Linear(sum(filter_nums), embed_size)

    def forward(self, words):
        """
        输入words的index后，生成对应的words的表示。

        :param words: [batch_size, max_len]
        :return: [batch_size, max_len, embed_size]
        """
        batch_size, max_len = words.size()
        chars = self.words_to_chars_embedding[words]  # batch_size x max_len x max_word_len
        word_lengths = self.word_lengths[words] # batch_size x max_len
        max_word_len = word_lengths.max()
        chars = chars[:, :, :max_word_len]
        # 为1的地方为mask
        chars_masks = chars.eq(self.char_pad_index)  # batch_size x max_len x max_word_len 如果为0, 说明是padding的位置了
        chars = self.char_embedding(chars)  # batch_size x max_len x max_word_len x embed_size

        reshaped_chars = chars.reshape(batch_size*max_len, max_word_len, -1)
        reshaped_chars = reshaped_chars.transpose(1, 2)  # B' x E x M
        conv_chars = [conv(reshaped_chars).transpose(1, 2).reshape(batch_size, max_len, max_word_len, -1)
                      for conv in self.convs]
        conv_chars = torch.cat(conv_chars, dim=-1).contiguous()  # B x max_len x max_word_len x sum(filters)
        conv_chars = self.activation(conv_chars)
        if self.pool_method == 'max':
            conv_chars = conv_chars.masked_fill(chars_masks.unsqueeze(-1), float('-inf'))
            chars, _ = torch.max(conv_chars, dim=-2) # batch_size x max_len x sum(filters)
        else:
            conv_chars = conv_chars.masked_fill(chars_masks.unsqueeze(-1), 0)
            chars = torch.sum(conv_chars, dim=-2)/chars_masks.eq(0).sum(dim=-1, keepdim=True).float()
        chars = self.fc(chars)
        return chars

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许
        :return:
        """
        params = []
        for name, param in self.named_parameters():
            if 'words_to_chars_embedding' not in name and 'word_lengths' not in name:
                params.append(param.requires_grad)
        requires_grads = set(params)
        if len(requires_grads) == 1:
            return requires_grads.pop()
        else:
            return None

    @requires_grad.setter
    def requires_grad(self, value):
        for name, param in self.named_parameters():
            if 'words_to_chars_embedding' in name or 'word_lengths' in name:  # 这个不能加入到requires_grad中
                pass
            param.requires_grad = value


class LSTMCharEmbedding(TokenEmbedding):
    """
    别名：:class:`fastNLP.modules.LSTMCharEmbedding`   :class:`fastNLP.modules.encoder.embedding.LSTMCharEmbedding`

    使用LSTM的方式对character进行encode.

    Example::

        >>>

    :param vocab: 词表
    :param embed_size: embedding的大小。默认值为50.
    :param char_emb_size: character的embedding的大小。默认值为50.
    :param hidden_size: LSTM的中间hidden的大小，如果为bidirectional的，hidden会除二，默认为50.
    :param pool_method: 支持'max', 'avg'
    :param activation: 激活函数，支持'relu', 'sigmoid', 'tanh', 或者自定义函数.
    :param min_char_freq: character的最小出现次数。默认值为2.
    :param bidirectional: 是否使用双向的LSTM进行encode。默认值为True。
    """
    def __init__(self, vocab: Vocabulary, embed_size: int=50, char_emb_size: int=50, hidden_size=50,
                 pool_method: str='max', activation='relu', min_char_freq: int=2, bidirectional=True):
        super(LSTMCharEmbedding, self).__init__(vocab)

        assert hidden_size % 2 == 0, "Only even kernel is allowed."

        assert pool_method in ('max', 'avg')
        self.pool_method = pool_method

        # activation function
        if isinstance(activation, str):
            if activation.lower() == 'relu':
                self.activation = F.relu
            elif activation.lower() == 'sigmoid':
                self.activation = F.sigmoid
            elif activation.lower() == 'tanh':
                self.activation = F.tanh
        elif activation is None:
            self.activation = lambda x: x
        elif callable(activation):
            self.activation = activation
        else:
            raise Exception(
                "Undefined activation function: choose from: [relu, tanh, sigmoid, or a callable function]")

        print("Start constructing character vocabulary.")
        # 建立char的词表
        self.char_vocab = _construct_char_vocab_from_vocab(vocab, min_freq=min_char_freq)
        self.char_pad_index = self.char_vocab.padding_idx
        print(f"In total, there are {len(self.char_vocab)} distinct characters.")
        # 对vocab进行index
        self.max_word_len = max(map(lambda x: len(x[0]), vocab))
        self.words_to_chars_embedding = nn.Parameter(torch.full((len(vocab), self.max_word_len),
                                                                fill_value=self.char_pad_index, dtype=torch.long),
                                                     requires_grad=False)
        self.word_lengths = nn.Parameter(torch.zeros(len(vocab)).long(), requires_grad=False)
        for word, index in vocab:
            # if index!=vocab.padding_idx:  # 如果是pad的话，直接就为pad_value了. 修改为不区分pad与否
            self.words_to_chars_embedding[index, :len(word)] = \
                torch.LongTensor([self.char_vocab.to_index(c) for c in word])
            self.word_lengths[index] = len(word)
        self.char_embedding = nn.Embedding(len(self.char_vocab), char_emb_size)

        self.fc = nn.Linear(hidden_size, embed_size)
        hidden_size = hidden_size // 2 if bidirectional else hidden_size

        self.lstm = LSTM(char_emb_size, hidden_size, bidirectional=bidirectional, batch_first=True)
        self._embed_size = embed_size
        self.bidirectional = bidirectional

    def forward(self, words):
        """
        输入words的index后，生成对应的words的表示。

        :param words: [batch_size, max_len]
        :return: [batch_size, max_len, embed_size]
        """
        batch_size, max_len = words.size()
        chars = self.words_to_chars_embedding[words]  # batch_size x max_len x max_word_len
        word_lengths = self.word_lengths[words]  # batch_size x max_len
        max_word_len = word_lengths.max()
        chars = chars[:, :, :max_word_len]
        # 为mask的地方为1
        chars_masks = chars.eq(self.char_pad_index)  # batch_size x max_len x max_word_len 如果为0, 说明是padding的位置了
        chars = self.char_embedding(chars)  # batch_size x max_len x max_word_len x embed_size

        reshaped_chars = chars.reshape(batch_size * max_len, max_word_len, -1)
        char_seq_len = chars_masks.eq(0).sum(dim=-1).reshape(batch_size * max_len)
        lstm_chars = self.lstm(reshaped_chars, char_seq_len)[0].reshape(batch_size, max_len, max_word_len, -1)
        # B x M x M x H

        lstm_chars = self.activation(lstm_chars)
        if self.pool_method == 'max':
            lstm_chars = lstm_chars.masked_fill(chars_masks.unsqueeze(-1), float('-inf'))
            chars, _ = torch.max(lstm_chars, dim=-2)  # batch_size x max_len x H
        else:
            lstm_chars = lstm_chars.masked_fill(chars_masks.unsqueeze(-1), 0)
            chars = torch.sum(lstm_chars, dim=-2) / chars_masks.eq(0).sum(dim=-1, keepdim=True).float()

        chars = self.fc(chars)

        return chars

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许
        :return:
        """
        params = []
        for name, param in self.named_parameters():
            if 'words_to_chars_embedding' not in name and 'word_lengths' not in name:
                params.append(param)
        requires_grads = set(params)
        if len(requires_grads) == 1:
            return requires_grads.pop()
        else:
            return None

    @requires_grad.setter
    def requires_grad(self, value):
        for name, param in self.named_parameters():
            if 'words_to_chars_embedding' in name or 'word_lengths' in name:  # 这个不能加入到requires_grad中
                pass
            param.requires_grad = value


class StackEmbedding(TokenEmbedding):
    """
    别名：:class:`fastNLP.modules.StackEmbedding`   :class:`fastNLP.modules.encoder.embedding.StackEmbedding`

    支持将多个embedding集合成一个embedding。

    Example::

        >>>


    :param embeds: 一个由若干个TokenEmbedding组成的list，要求每一个TokenEmbedding的词表都保持一致

    """
    def __init__(self, embeds: List[TokenEmbedding]):
        vocabs = []
        for embed in embeds:
            vocabs.append(embed.get_word_vocab())
        _vocab = vocabs[0]
        for vocab in vocabs[1:]:
            assert vocab == _vocab, "All embeddings should use the same word vocabulary."

        super(StackEmbedding, self).__init__(_vocab)
        assert isinstance(embeds, list)
        for embed in embeds:
            assert isinstance(embed, TokenEmbedding), "Only TokenEmbedding type is supported."
        self.embeds = nn.ModuleList(embeds)
        self._embed_size = sum([embed.embed_size for embed in self.embeds])

    def append(self, embed: TokenEmbedding):
        """
        添加一个embedding到结尾。
        :param embed:
        :return:
        """
        assert isinstance(embed, TokenEmbedding)
        self.embeds.append(embed)

    def pop(self):
        """
        弹出最后一个embed
        :return:
        """
        return self.embeds.pop()

    @property
    def embed_size(self):
        return self._embed_size

    @property
    def requires_grad(self):
        """
        Embedding的参数是否允许优化。True: 所有参数运行优化; False: 所有参数不允许优化; None: 部分允许优化、部分不允许
        :return:
        """
        requires_grads = set([embed.requires_grad for embed in self.embeds()])
        if len(requires_grads)==1:
            return requires_grads.pop()
        else:
            return None

    @requires_grad.setter
    def requires_grad(self, value):
        for embed in self.embeds():
            embed.requires_grad = value

    def forward(self, words):
        """
        得到多个embedding的结果，并把结果按照顺序concat起来。

        :param words: batch_size x max_len
        :return: 返回的shape和当前这个stack embedding中embedding的组成有关
        """
        outputs = []
        for embed in self.embeds:
            outputs.append(embed(words))
        return torch.cat(outputs, dim=-1)

