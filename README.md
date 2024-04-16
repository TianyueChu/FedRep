# FedRep

The reputation-based aggregation for Federated Learning 


## Requirements
This code requires the following:
- Python 3.6 or greater
- PyTorch 1.6 or greater
- Torchvision
- Numpy 1.18.5


## Data Preparation

-   Download train and test datasets manually from the given links, or they will use the default links in torchvision.
-   Experiments are run on MNIST, Fashion-MNIST and CIFAR10. [http://yann.lecun.com/exdb/mnist/](http://yann.lecun.com/exdb/mnist/) [https://github.com/zalandoresearch/fashion-mnist](https://github.com/zalandoresearch/fashion-mnist) [http://www.cs.toronto.edu/∼kriz/cifar.html](http://www.cs.toronto.edu/%E2%88%BCkriz/cifar.html)

You can change the default values of other parameters to simulate different conditions. Refer to [options.py](utils/options.py).

## Options

The default values for various parameters parsed to the experiment are given in `options.py`. Details are given some of those parameters:

-   `--dataset:` Default is 'mnist'. Options: 'mnist', 'cifar'
-   `--iid:` Defaul is False. 
-   `--seed:` Random Seed. Default is 1.
-   `--model:` Local model. Default is 'cnn'. Options:  'cnn', 'resnet18'
-   `--agg:`Aggregation methods. Default is 'fedavg'. Options: 'median', 'trimmed-mean', 'irls'.
-   `--epochs:` Rounds of training. Default is 100.
-   `--frac:`The fraction of parties. Default is 0.1.


## References
Chu, Tianyue, Alvaro Garcia-Recuero, Costas Iordanou, Georgios Smaragdakis, and Nikolaos Laoutaris. "Securing Federated Sensitive Topic Classification against Poisoning Attacks." NDSS 2023.

