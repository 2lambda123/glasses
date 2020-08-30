from __future__ import annotations
from torch import nn
from torch import Tensor
from ....blocks.residuals import ResidualAdd
from ....blocks import Conv2dPad, ConvBnAct
from collections import OrderedDict
from ..resnet import ResnetDecoder
from typing import List
from functools import partial


"""Implementations of ResNet proposed in `Deep Residual Learning for Image Recognition <https://arxiv.org/abs/1512.03385>`
"""


ReLUInPlace = partial(nn.ReLU, inplace=True)


class DepthWiseConv2d(Conv2dPad):
    def __init__(self, in_channels, out_channels, *args, **kwargs):
        super().__init__(in_channels, out_channels, *args, groups=in_channels, **kwargs)


class InvertedResidualBlock(nn.Module):
    """This block use a depth wise and point wise convolution to reduce computation cost.
    First the input is expansed (if needed) by a 1x1 conv, then a depth wise 3x3 conv and point wise conv are applied.

    .. image:: https://github.com/FrancescoSaverioZuppichini/glasses/blob/develop/docs/_static/images/MobileNetBasicBlock.png?raw=true


    ReLU6 is the default activation because it was found to be more robust when used with low-precision computation.

    Residual connections are not applied when input and output's dimensions matches (stride > 1).

    .. image:: https://github.com/FrancescoSaverioZuppichini/glasses/blob/develop/docs/_static/images/MobileNetBasicBlockNoRes.png?raw=true

    Args:
        in_features (int): [description]
        out_features (int): [description]
        activation (nn.Module, optional): [description]. Defaults to nn.ReLU6.
        downsampling (int, optional): [description]. Defaults to 1.
    """

    def __init__(self, in_features: int, out_features: int,  activation: nn.Module = nn.ReLU6, downsampling: int = 1, expansion: int = 6):
        super().__init__()
        self.in_features, self.out_features = in_features, out_features
        self.expansion = expansion
        self.expanded_features = in_features * self.expansion

        weights = nn.Sequential()
        # we need to expandn the input only if expansion is greater than one
        if expansion > 1:
            weights.add_module('exp', ConvBnAct(in_features,  self.expanded_features,
                                                activation=activation, kernel_size=1, bias=False))
        # add the depth wise and point wise conv
        weights.add_module('conv',
                           nn.Sequential(ConvBnAct(self.expanded_features, self.expanded_features,
                                                   conv=DepthWiseConv2d,
                                                   activation=activation,
                                                   kernel_size=3,
                                                   stride=downsampling, bias=False),
                                         Conv2dPad(self.expanded_features,
                                                   out_features, kernel_size=1, bias=False),
                                         nn.BatchNorm2d(out_features))
                           )
        # do not apply residual when downsamping and when features are different
        # in mobilenet we do not use a shortcut
        self.block = ResidualAdd(
            weights) if downsampling == 1 and in_features == out_features else weights

    def forward(self, x: Tensor) -> Tensor:
        x = self.block(x)
        return x


MobileNetBasicBlock = InvertedResidualBlock


class MobileNetLayer(nn.Module):
    def __init__(self, in_features: int, out_features: int, block: nn.Module = MobileNetBasicBlock, n: int = 1, downsampling: int = 1, *args, **kwargs):
        super().__init__()
        self.block = nn.Sequential(
            block(in_features, out_features, *args,
                  downsampling=downsampling,  **kwargs),
            *[block(out_features,
                    out_features, *args, **kwargs) for _ in range(n - 1)]
        )

    def forward(self, x: Tensor) -> Tensor:
        x = self.block(x)
        return x


class MobileNetEncoder(nn.Module):
    """
    MobileNet encoder composed by different layers with increasing features size.
    Please refer to Table 2 in the original paper for an overview of the current architecture

    Args:
            in_channels (int, optional): [description]. Defaults to 3.
            blocks_sizes (List[int], optional): Number of features for each layer, called `c` in the paper. Defaults to [32, 16, 24, 32, 64, 96, 160, 320].
            depths (List[int], optional): Number of blocks at each layer, called `n` in the paper. Defaults to [1, 1, 2, 3, 4, 3, 3, 1].
            strides (List[int], optional): Number of stride for each layer, called `s` in the paper. Defaults to [2, 1, 2, 2, 2, 1, 2, 1].
            expansions (List[int], optional): Expansion for each block in each layer, called `t` in the paper. Defaults to [1, 6, 6, 6, 6, 6, 6].
            activation (nn.Module, optional): [description]. Defaults to nn.ReLU6.
            block (nn.Module, optional): [description]. Defaults to MobileNetBasicBlock.
    """

    def __init__(self, in_channels: int = 3, blocks_sizes: List[int] = [32, 16, 24, 32, 64, 96, 160, 320, 1280],
                 depths: List[int] = [1, 1, 2, 3, 4, 3, 3, 1],
                 strides: List[int] = [2, 1, 2, 2, 2, 1, 2, 1],
                 expansions: List[int] = [1, 6, 6, 6, 6, 6, 6],
                 activation: nn.Module = nn.ReLU6, block: nn.Module = MobileNetBasicBlock, *args, **kwargs):
        super().__init__()
        # TODO mostly copied from resnet, we should find a way to re use the resnet one!
        self.blocks_sizes = blocks_sizes

        self.gate = nn.Sequential(
            ConvBnAct(in_channels, blocks_sizes[0], activation=activation,
                      kernel_size=3, stride=strides[0], bias=False),
        )

        self.in_out_block_sizes = list(zip(blocks_sizes, blocks_sizes[1:-1]))

        self.blocks = nn.ModuleList([
            *[MobileNetLayer(in_channels,
                             out_channels, n=n, downsampling=s, activation=activation,
                             block=block, *args,  expansion=t, **kwargs)
              for (in_channels, out_channels), n, s, t in zip(self.in_out_block_sizes, depths[1:], strides[1:], expansions)]
        ])

        self.blocks.append(nn.Sequential(
            ConvBnAct(blocks_sizes[-2], blocks_sizes[-1],
                      activation=nn.ReLU6, kernel_size=1, bias=False),
        ))

        self.initialize()

    def initialize(self):
        # weight initialization
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                nn.init.zeros_(m.bias)

    def forward(self, x):
        x = self.gate(x)
        for block in self.blocks:
            x = block(x)

        return x


class MobileNetV2(nn.Module):
    """Implementations of MobileNet v2 proposed in `MobileNetV2: Inverted Residuals and Linear Bottlenecks <https://arxiv.org/pdf/1801.04381.pdf>`_

    .. image:: https://github.com/FrancescoSaverioZuppichini/glasses/blob/develop/docs/_static/images/MobileNet.png?raw=true

    Create a default model

    Examples:
        >>> MobileNetV2()

    Customization

    You can easily customize your mobilenet

    Examples:
        >>> MobileNetV2(activation = nn.SELU)
        >>> # change number of classes (default is 1000 )
        >>> MobileNetV2(n_classes=100)
        >>> # pass a different block
        >>> class SEInvertedResidualBlock(InvertedResidualBlock):
        >>>     def __init__(self, in_features, out_features, *args, **kwargs):
        >>>         super().__init__(in_features, out_features, *args, **kwargs)
        >>>         self.block.add_module('se', SEModule(out_features))
        >>> # now mobile net has squeeze and excitation!
        >>> MobileNetV2(block=SEInvertedResidualBlock)
        >>> # change the initial convolution
        >>> model = MobileNetV2()
        >>> model.encoder.gate[0].conv = nn.Conv2d(3, 32, kernel_size=7)
        >>> # store each feature
        >>> x = torch.rand((1, 3, 224, 224))
        >>> model =MobileNetV2()
        >>> features = []
        >>> x = model.encoder.gate(x)
        >>> for block in model.encoder.blocks:
        >>>     x = block(x)
        >>>     features.append(x)
        >>> print([x.shape for x in features])
        >>> [torch.Size([1, 16, 112, 112]), torch.Size([1, 24, 56, 56]), torch.Size([1, 32, 28, 28]), torch.Size([1, 64, 14, 14]), torch.Size([1, 96, 14, 14]), torch.Size([1, 160, 7, 7]), torch.Size([1, 320, 7, 7]), torch.Size([1, 1280, 7, 7])]

    Args:
        in_channels (int, optional): Number of channels in the input Image (3 for RGB and 1 for Gray). Defaults to 3.
        n_classes (int, optional): Number of classes. Defaults to 1000.
    """

    def __init__(self, in_channels: int = 3, n_classes: int = 1000, *args, **kwargs):
        super().__init__()
        self.encoder = MobileNetEncoder(in_channels, *args, **kwargs)
        self.decoder = ResnetDecoder(
            self.encoder.blocks_sizes[-1], n_classes)

        self.initialize()

    def initialize(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(self, x: Tensor) -> Tensor:
        x = self.encoder(x)
        x = self.decoder(x)
        return x
