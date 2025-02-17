#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.

import logging

import torch
from reagent.core.configuration import resolve_defaults
from reagent.core.dataclasses import field
from reagent.core.parameters import RLParameters
from reagent.optimizer.union import Optimizer__Union
from reagent.training.rl_trainer_pytorch import RLTrainer


logger = logging.getLogger(__name__)


class ImitatorTrainer(RLTrainer):
    @resolve_defaults
    def __init__(
        self,
        imitator,
        use_gpu: bool = False,
        rl: RLParameters = field(default_factory=RLParameters),  # noqa: B008
        minibatch_size: int = 1024,
        minibatches_per_step: int = 1,
        optimizer: Optimizer__Union = field(  # noqa: B008
            default_factory=Optimizer__Union.default
        ),
    ) -> None:
        super().__init__(rl, use_gpu=use_gpu)
        self.minibatch_size = minibatch_size
        self.minibatches_per_step = minibatches_per_step or 1
        self.imitator = imitator
        self.imitator_optimizer = optimizer.make_optimizer(imitator.parameters())

    def _imitator_accuracy(self, predictions, true_labels):
        match_tensor = predictions == true_labels
        matches = int(match_tensor.sum())
        return round(matches / len(predictions), 3)

    @torch.no_grad()
    def train(self, training_batch, train=True):
        learning_input = training_batch.training_input

        with torch.enable_grad():
            action_preds = self.imitator(learning_input.state.float_features)
            # Classification label is index of action with value 1
            pred_action_idxs = torch.max(action_preds, dim=1)[1]
            actual_action_idxs = torch.max(learning_input.action, dim=1)[1]

            if train:
                imitator_loss = torch.nn.CrossEntropyLoss()
                bcq_loss = imitator_loss(action_preds, actual_action_idxs)
                bcq_loss.backward()
                self._maybe_run_optimizer(
                    self.imitator_optimizer, self.minibatches_per_step
                )

        return self._imitator_accuracy(pred_action_idxs, actual_action_idxs)


def get_valid_actions_from_imitator(imitator, input, drop_threshold):
    """Create mask for non-viable actions under the imitator."""
    if isinstance(imitator, torch.nn.Module):
        # pytorch model
        imitator_outputs = imitator(input.float_features)
        on_policy_action_probs = torch.nn.functional.softmax(imitator_outputs, dim=1)
    else:
        # sci-kit learn model
        on_policy_action_probs = torch.tensor(imitator(input.float_features.cpu()))

    filter_values = (
        on_policy_action_probs / on_policy_action_probs.max(keepdim=True, dim=1)[0]
    )
    return (filter_values >= drop_threshold).float()
