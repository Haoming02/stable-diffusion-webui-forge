import torch
from modules import prompt_parser, devices, sd_samplers_common

from modules.shared import opts, state
import modules.shared as shared
from modules.script_callbacks import CFGDenoiserParams, cfg_denoiser_callback
from modules.script_callbacks import CFGDenoisedParams, cfg_denoised_callback
from modules.script_callbacks import AfterCFGCallbackParams, cfg_after_cfg_callback
from modules_forge import forge_sampler


def catenate_conds(conds):
    if not isinstance(conds[0], dict):
        return torch.cat(conds)

    return {key: torch.cat([x[key] for x in conds]) for key in conds[0].keys()}


def subscript_cond(cond, a, b):
    if not isinstance(cond, dict):
        return cond[a:b]

    return {key: vec[a:b] for key, vec in cond.items()}


def pad_cond(tensor, repeats, empty):
    if not isinstance(tensor, dict):
        return torch.cat([tensor, empty.repeat((tensor.shape[0], repeats, 1))], axis=1)

    tensor['crossattn'] = pad_cond(tensor['crossattn'], repeats, empty)
    return tensor


class CFGDenoiser(torch.nn.Module):
    """
    Classifier free guidance denoiser. A wrapper for stable diffusion model (specifically for unet)
    that can take a noisy picture and produce a noise-free picture using two guidances (prompts)
    instead of one. Originally, the second prompt is just an empty string, but we use non-empty
    negative prompt.
    """

    def __init__(self, sampler):
        super().__init__()
        self.model_wrap = None
        self.mask = None
        self.nmask = None
        self.init_latent = None
        self.steps = None
        """number of steps as specified by user in UI"""

        self.total_steps = None
        """expected number of calls to denoiser calculated from self.steps and specifics of the selected sampler"""

        self.step = 0
        self.image_cfg_scale = None
        self.padded_cond_uncond = False
        self.padded_cond_uncond_v0 = False
        self.sampler = sampler
        self.model_wrap = None
        self.p = None

        # Backward Compatibility
        self.mask_before_denoising = False

        self.classic_ddim_eps_estimation = False

    @property
    def inner_model(self):
        raise NotImplementedError()

    def combine_denoised(self, x_out, conds_list, uncond, cond_scale, timestep, x_in, cond):
        denoised_uncond = x_out[-uncond.shape[0]:]
        denoised = torch.clone(denoised_uncond)

        for i, conds in enumerate(conds_list):
            for cond_index, weight in conds:
                denoised[i] += (x_out[cond_index] - denoised_uncond[i]) * (weight * cond_scale)

        return denoised

    def combine_denoised_for_edit_model(self, x_out, cond_scale):
        out_cond, out_img_cond, out_uncond = x_out.chunk(3)
        denoised = out_uncond + cond_scale * (out_cond - out_img_cond) + self.image_cfg_scale * (out_img_cond - out_uncond)

        return denoised

    def get_pred_x0(self, x_in, x_out, sigma):
        return x_out

    def update_inner_model(self):
        self.model_wrap = None

        c, uc = self.p.get_conds()
        self.sampler.sampler_extra_args['cond'] = c
        self.sampler.sampler_extra_args['uncond'] = uc

    def forward(self, x, sigma, uncond, cond, cond_scale, s_min_uncond, image_cond):
        if state.interrupted or state.skipped:
            raise sd_samplers_common.InterruptedException

        original_x_device = x.device
        original_x_dtype = x.dtype

        if self.classic_ddim_eps_estimation:
            acd = self.inner_model.inner_model.alphas_cumprod
            fake_sigmas = ((1 - acd) / acd) ** 0.5
            real_sigma = fake_sigmas[sigma.round().long().clip(0, int(fake_sigmas.shape[0]))]
            real_sigma_data = 1.0
            x = x * (((real_sigma ** 2.0 + real_sigma_data ** 2.0) ** 0.5)[:, None, None, None])
            sigma = real_sigma

        if sd_samplers_common.apply_refiner(self, x):
            cond = self.sampler.sampler_extra_args['cond']
            uncond = self.sampler.sampler_extra_args['uncond']

        cond_composition, cond = prompt_parser.reconstruct_multicond_batch(cond, self.step)
        uncond = prompt_parser.reconstruct_cond_batch(uncond, self.step)

        if self.mask is not None:
            noisy_initial_latent = self.init_latent + sigma[:, None, None, None] * torch.randn_like(self.init_latent).to(self.init_latent)
            x = x * self.nmask + noisy_initial_latent * self.mask

        denoiser_params = CFGDenoiserParams(x, image_cond, sigma, state.sampling_step, state.sampling_steps, cond, uncond, self)
        cfg_denoiser_callback(denoiser_params)

        if 0.0 <= self.step / self.total_steps <= opts.skip_early_cond:
            cond_scale = 1.0
        if s_min_uncond > 0.0 and sigma[0] < s_min_uncond:
            cond_scale = 1.0

        denoised = forge_sampler.forge_sample(self, denoiser_params=denoiser_params,
                                              cond_scale=cond_scale, cond_composition=cond_composition)

        if self.mask is not None:
            denoised = denoised * self.nmask + self.init_latent * self.mask

        preview = self.sampler.last_latent = denoised
        sd_samplers_common.store_latent(preview)

        after_cfg_callback_params = AfterCFGCallbackParams(denoised, state.sampling_step, state.sampling_steps)
        cfg_after_cfg_callback(after_cfg_callback_params)
        denoised = after_cfg_callback_params.x

        self.step += 1

        if self.classic_ddim_eps_estimation:
            eps = (x - denoised) / sigma[:, None, None, None]
            return eps

        return denoised.to(device=original_x_device, dtype=original_x_dtype)

