#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Python version: 3.6

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import copy
import numpy as np
from tqdm import tqdm
import torch
from datasets import build_datasets
from tensorboardX import SummaryWriter
from options import args_parser
from Update import LocalUpdate
from FedNets import build_model
from averaging import aggregate_weights, get_valid_models, FoolsGold, IRLS_aggregation_split_restricted
from attack import add_gaussian_noise, change_weight
from URL.URLHelper import URLHelper
import pickle

os.environ['CUDA_LAUNCH_BLOCKING'] = "1"

avg_acc = []
avg_loss_test = []

def test(net_g, dataset, args, dict_users):
    global avg_acc, avg_loss_test
    # testing
    list_acc = []
    list_loss = []
    net_test = copy.deepcopy(net_g)
    net_test.eval()
    if args.dataset == "mnist":
        classes = ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')
    elif args.dataset == "cifar":
        classes = ('plane', 'car', 'bird', 'cat',
                   'deer', 'dog', 'frog', 'horse', 'ship', 'truck')
    elif args.dataset == "loan":
        classes = ('0', '1', '2', '3', '4', '5', '6', '7', '8')
    elif args.dataset == "URL":
        classes = ('0', '1', '2', '3', '4', '5')

    with torch.no_grad():
        added_acc=0
        added_loss=0
        added_data_num=0
        for c in range(len(classes)):
            if c < len(classes) and len(dict_users[c])>0:

                net_local = LocalUpdate(args=args, dataset=dataset, idxs=dict_users[c], tb=None, test_flag=True)
                acc, loss = net_local.test(net=net_test)
                # print('acc is ', acc)
                # print("test accuracy for label {} is {:.2f}%".format(classes[c], acc * 100.))
                list_acc.append(acc)
                list_loss.append(loss)
                added_acc+= acc*len(dict_users[c])
                added_loss+= loss*len(dict_users[c])
                added_data_num+=len(dict_users[c])

        print("average acc: {:.2f}%,\ttest loss: {:.5f}".format(100. * added_acc/ float(added_data_num),
                                                                added_loss/ float(added_data_num)))
        avg_acc.append(100. * added_acc/ float(added_data_num))
        avg_loss_test.append(added_loss/ float(added_data_num))
        
    return list_acc


def backdoor_test(net_g, dataset, args, idxs):
    # backdoor testing
    net_test = copy.deepcopy(net_g)
    net_test.eval()
    with torch.no_grad():
        net_local = LocalUpdate(args=args, dataset=dataset, idxs=idxs, tb=None,
                                backdoor_label=args.backdoor_label, test_flag=True)
        acc, loss = net_local.backdoor_test(net=net_test)
        #if acc is None:
            #acc = 0
        #if loss is None:
            #loss = 10000
        print("backdoor acc: {:.2f}%,\t test loss: {:.5f}".format(acc*100., loss))
    return acc


if __name__ == '__main__':
    # parse args
    args = args_parser()
    np.random.seed(args.seed)
    learning_rate = args.lr
    # set attack mode
    print('perform poison attack with {} attackers'.format(args.num_attackers))

    # define paths
    path_project = os.path.abspath('..')
    if args.gpu != -1:
        torch.cuda.set_device(args.gpu)

    summary = SummaryWriter('local')

    dataset_train, dataset_test, dict_users, test_users, attackers = build_datasets(args)
    print(type(dataset_train))
    
    if not args.fix_total:
        args.num_users += args.num_attackers

    # check poison attack
    if args.dataset == 'mnist' and args.num_attackers > 0:
        if args.attack_label:
            assert args.attack_label == 1
        else:
            pass

    #elif args.dataset == 'cifar' and args.num_attackers > 0:
        #assert args.attack_label == 3
    elif args.dataset == 'loan' and args.num_attackers > 0:
        assert args.attack_label == 0
    elif args.dataset == 'mnist' and args.num_attackers < 0:
        assert (args.donth_attack and not args.attack_label)

    # build model
    if args.gpu != -1:
        torch.cuda.set_device(args.gpu)
    net_glob = build_model(args)

    print(net_glob)

    # init FoolsGold
    if args.agg == 'fg':
        fg = FoolsGold(args)
    else:
        fg = None


    # backdoor for IMC initialization
    csv_file = '../data/sensitive_websites_dataset_clean.csv'
    uh = URLHelper(csv_file).back_door('Health')

    # copy weights
    w_glob = net_glob.state_dict()
    net_glob.train()

    ######################################################
    # Training                                           #
    ######################################################
    loss_train = []
    accs = []
    reweights = []
    backdoor_accs = []
    # local_weight initialization
    local_weight = []
    for i in range(args.num_users):
        local_weight.append([])
        
    # reputation initialization
    reputation_effect = []
    for i in range(args.num_users):
        reputation_effect.append([])

    if 'irls' in args.agg:
        model_save_path = '../weights/{}_{}_irls_{}_{}_{}'.format(args.dataset, args.model, args.Lambda, args.thresh, args.iid)
    else:
        model_save_path = '../weights/{}_{}'.format(args.dataset, args.model)
    if not os.path.exists(model_save_path):
        os.makedirs(model_save_path)
    last_bd_acc = 0
    lr_change_ratio = 1
    for iter in tqdm(range(args.epochs)):
        print('Epoch:', iter, "/", args.epochs)
        net_glob.train()
        w_locals, loss_locals = [], []
        m = max(int(args.frac * args.num_users), 1)
        # print('taking {} users'.format(m))
        if args.frac == 1:
            idxs_users = range(args.num_users)
        else:
            idxs_users = np.random.choice(range(args.num_users), m, replace=False)
        # print('idxs_users is ', idxs_users)

        for idx in idxs_users:
            # print('idx is ', idx)
            if (idx >= args.num_users - args.num_attackers and not args.fix_total) or \
                    (args.fix_total and idx in attackers):
                local_ep = args.local_ep
                if args.attacker_ep != args.local_ep:
                    if args.dataset == 'URL':
                        lr_change_ratio = 1
                    args.lr = args.lr * lr_change_ratio
                args.local_ep = args.attacker_ep

                if args.is_backdoor:
                    if args.backdoor_single_shot_scale_epoch == -1 or iter == args.backdoor_single_shot_scale_epoch:
                        print('backdoor attacker', idx)
                        local = LocalUpdate(args=args, dataset=dataset_train, idxs=dict_users[idx], tb=summary,
                                        backdoor_label=args.backdoor_label)
                    else: # under one-single-shot mode; don't perform backdoor attack because it's not the scale epoch:
                        local = LocalUpdate(args=args, dataset=dataset_train, idxs=dict_users[idx], tb=summary)
                else:
                    if args.fix_total and args.attack_label < 0 and args.donth_attack:
                        continue
                    else:
                        local = LocalUpdate(args=args, dataset=dataset_train, idxs=dict_users[idx], tb=summary,
                                        attack_label=args.attack_label)

                temp_net = copy.deepcopy(net_glob)
                
                if args.agg == 'fg':
                    w, loss, _poisoned_net = local.update_gradients(net=temp_net)
                else:
                    w, loss, _poisoned_net = local.update_weights(net=temp_net)

                # change a portion of the model gradients to honest
                # if 0 < args.change_rate :
                    # Passing arguments to the new Reputation Model.
                    # w_honest, reweight = IRLS_aggregation_split_restricted(w_locals, args.reputation_active, reputation_effect, args.Lambda, args.thresh, args.kappa, args.W, args.a)
                    # w = change_weight(w, w_honest, change_rate=args.change_rate)
                args.local_ep = local_ep
                if args.attacker_ep != args.local_ep:
                    args.lr = args.lr / lr_change_ratio
            else:
                # print('dict_users[idx]',dict_users[idx])
                local = LocalUpdate(args=args, dataset=dataset_train, idxs=dict_users[idx], tb=summary)
                temp_net = copy.deepcopy(net_glob)
                if args.agg == 'fg':
                    w, loss, _updated_net = local.update_gradients(net=temp_net)
                else:
                    w, loss, _updated_net = local.update_weights(net=temp_net)

            w_locals.append(copy.deepcopy(w))
            loss_locals.append(copy.deepcopy(loss))

        # remove model with inf values
        w_locals, invalid_model_idx = get_valid_models(w_locals)

        if len(w_locals) == 0:
            continue
        
        #local_weight_copy = copy.deepcopy(w_locals[0])
        #for k in local_weight_copy.keys():
            # print("k",k)
            # print("len(w_locals)", len(w_locals))
            #for i in range(len(w_locals)):
                # print("i",i)
                #local_weight[i].extend(w_locals[i][k].detach().cpu().numpy())
         
           
        w_glob = aggregate_weights(args, w_locals, net_glob, reweights, fg, reputation_effect)

        # copy weight to net_glob
        if not args.agg == 'fg':
            net_glob.load_state_dict(w_glob)

        # test data
        list_acc = test(net_glob, dataset_test, args, test_users)
        accs.append(list_acc)

        # poisoned test data
        if args.is_backdoor:
            _backdoor_acc = backdoor_test(net_glob, dataset_test, args, np.asarray(list(range(len(dataset_test)))))
            backdoor_accs.append([_backdoor_acc])
            last_bd_acc = _backdoor_acc
        # print loss
        loss_avg = sum(loss_locals) / len(loss_locals)
        if args.epochs % 10 == 0:
            print('Train loss:', loss_avg)
        loss_train.append(loss_avg)
    
    print('loss_train',loss_train) 
    print('average accuracy',avg_acc)
    print('average test loss',avg_loss_test)
    #with open('local_weight.pkl', 'wb') as f:
        #pickle.dump(local_weight, f)
        
    save_folder='../save/'

    ######################################################
    # Testing                                            #
    ######################################################
    list_acc, list_loss = [], []
    net_glob.eval()
    if args.dataset == "mnist":
        classes = ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')
    elif args.dataset == "cifar":
        classes = ('plane', 'car', 'bird', 'cat',
                   'deer', 'dog', 'frog', 'horse', 'ship', 'truck')
    elif args.dataset == "loan":
        classes = ('0', '1', '2', '3', '4', '5', '6', '7', '8')
    elif args.dataset == "URL":
        classes = ('0', '1', '2', '3', '4', '5')

    added_acc = 0
    added_loss = 0
    added_data_num = 0
    with torch.no_grad():
        for c in range(len(classes)):
            if c < len(classes) and len(test_users[c])>0:
                net_local = LocalUpdate(args=args, dataset=dataset_test, idxs=test_users[c], tb=summary, test_flag=True)
                acc, loss = net_local.test(net=net_glob)
                print("test accuracy for label {} is {:.2f}%".format(classes[c], acc * 100.))
                list_acc.append(acc)
                list_loss.append(loss)
                # print('list_acc is',list_acc)
                # print('list_lost')
                added_acc += acc * len(test_users[c])
                added_loss += loss * len(test_users[c])
                added_data_num += len(test_users[c])
        print("average acc: {:.2f}%,\ttest loss: {:.5f}".format(100. * added_acc / float(added_data_num),
                                                           added_loss / float(added_data_num)))
        
        
        # backdoor attack
        if args.is_backdoor:
            acc = backdoor_test(net_glob, dataset_test, args, np.asarray(list(range(len(dataset_test)))))
            print("backdoor success rate for attacker is {:.2f}%".format(acc * 100.))

        # label-flipping attack
        else:
            if args.attack_label == -1:
               print("attack success rate for attacker is {:.2f}%".format(0 * 100.))
            else:
                net_local = LocalUpdate(args=args, dataset=dataset_test, idxs=test_users[args.attack_label], tb=summary,
                                    attack_label=args.attack_label,
                                    test_flag=True)
                acc, loss = net_local.test(net=net_glob)
                print("attack success rate for attacker is {:.2f}%".format(acc * 100.))
                
    

    ######################################################
    # Plot                                               #
    ######################################################
    # plot acc
    fig1 = plt.figure()
    ax1 = fig1.add_subplot(111)
    accs_np = np.zeros((len(accs), len(accs[0])))
    for i, w in enumerate(accs):
        accs_np[i] = np.array(w)
    accs_np = accs_np.transpose()
    colormap = plt.cm.gist_ncar  # nipy_spectral, Set1,Paired
    colors = [colormap(i) for i in np.linspace(0, 0.9, len(accs_np))]
    plt.ylabel('accuracy'.format(i))
    for i, y in enumerate(accs_np):
        plt.plot(range(len(y)), y)
    for i, j in enumerate(ax1.lines):
        j.set_color(colors[i])
    plt.legend([str(i) for i in range(len(accs_np))], loc='lower right')
    plt.savefig(
        save_folder+'acc_{}_{}_{}_{}_users{}_attackers_{}_attackep_{}_thresh_{}_iid{}.png'.format(args.agg,args.dataset,
                                                                                           args.model, args.epochs,
                                                                                           args.num_users - args.num_attackers,
                                                                                           args.num_attackers,
                                                                                           args.attacker_ep,
                                                                                           args.thresh,args.iid))
    
    
    # plot loss curve
    plt.figure()
    plt.plot(range(len(loss_train)), loss_train)
    plt.ylabel('train_loss')
    plt.savefig(save_folder+'fed_{}_{}_{}_{}_C{}_iid{}_users{}_attackers_{}_reputation_{}.png'.format(args.agg,args.dataset, args.model, args.epochs, args.frac, args.iid, args.num_users - args.num_attackers, args.num_attackers, args.reputation_active))

    if args.is_backdoor:
        # plot backdoor acc
        fig1 = plt.figure()
        ax1 = fig1.add_subplot(111)
        backdoor_accs_np = np.zeros((len(backdoor_accs), len(backdoor_accs[0])))
        for i, w in enumerate(backdoor_accs):
            backdoor_accs_np[i] = np.array(w)
        backdoor_accs_np = backdoor_accs_np.transpose()
        colormap = plt.cm.gist_ncar  # nipy_spectral, Set1,Paired
        colors = [colormap(i) for i in np.linspace(0, 0.9, len(backdoor_accs_np))]
        plt.ylabel('backdoor success rate'.format(i))
        for i, y in enumerate(backdoor_accs_np):
            plt.plot(range(len(y)), y)
        for i, j in enumerate(ax1.lines):
            j.set_color(colors[i])
        plt.legend([str(i) for i in range(len(backdoor_accs_np))], loc='lower right')
        plt.savefig(save_folder+'backdoor_accs_{}_{}_{}_{}_users{}_attackers_{}_attackep_{}_thresh_{}_iid{}.png'.format(args.agg,args.dataset,
                                                                                                         args.model,
                                                                                                         args.epochs,
                                                                                                         args.num_users - args.num_attackers,
                                                                                                         args.num_attackers,
                                                                                                         args.attacker_ep,
                                                                                                         args.thresh, args.iid))
