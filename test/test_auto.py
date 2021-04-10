import pytest
from glasses.models import AutoTransform, AutoModel, ResNet
from glasses.utils.weights.PretrainedWeightsProvider import (
    Config,
    PretrainedWeightsProvider,
    pretrained,
)
from torch import nn

from torchinfo.torchinfo import ModelStatistics


class Dummy(nn.Sequential):
    def __init__(self, *args, **kwargs):
        super().__init__(nn.Conv2d(3, 32, kernel_size=3))

    @classmethod
    @pretrained()
    def dummy(cls, *args, **kwargs):
        return cls()


AutoModel.zoo["dummy"] = Dummy.dummy


def test_AutoModel():
    model = AutoModel.from_name("dummy")
    assert isinstance(model, Dummy)
    # model = AutoModel.from_pretrained("dummy")
    # model = AutoModel.from_pretrained("dummy", excluding=lambda x: x[0])
    assert isinstance(model, Dummy)
    # with pytest.raises(KeyError):
    #     model = AutoModel.from_name("resn")
    # with pytest.raises(KeyError):
    #     model = AutoModel.from_name("resnetasddsadas")
    # with pytest.raises(KeyError):
    #     model = AutoModel.from_pretrained("resnetasddsadas")
    # with pytest.raises(KeyError):
    #     model = AutoModel.from_pretrained("resn")
    with pytest.raises(EnvironmentError):
        AutoModel()
    assert len(list(AutoModel.models())) > 0
    assert len(list(AutoModel.pretrained_models())) > 0

    assert len(list(AutoModel.models_table().columns[0].cells)) > 0

    assert type(AutoModel.from_name("resnet18").summary()) == ModelStatistics


def test_AutoTransform():
    cfg = AutoTransform.from_name("resnet18")
    assert isinstance(cfg, Config)
    cfg = AutoTransform.from_name("resnetasddsadas")
    assert len(list(AutoTransform.names())) > 0
    with pytest.raises(EnvironmentError):
        AutoTransform()
