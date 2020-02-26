import argparse
import sys

import gym
from gym import wrappers, logger
import tensorflow as tf

sys.path.append('./baselines/')
from baselines.ppo2.model import Model
from baselines.common.policies import build_policy
from baselines.common.cmd_util import make_vec_env
from baselines.common.vec_env.vec_frame_stack import VecFrameStack
from baselines.common.vec_env.vec_normalize import VecNormalize
from baselines.common.vec_env.vec_video_recorder import VecVideoRecorder
import matplotlib.pyplot as plt
from baselines.common.trex_utils import preprocess, mask_score

class RandomAgent(object):
    """The world's simplest agent!"""
    def __init__(self, action_space):
        self.action_space = action_space

    def act(self, observation, reward, done):
        return self.action_space.sample()

class PPO2Agent(object):
    def __init__(self, env, env_type, stochastic):
        ob_space = env.observation_space
        ac_space = env.action_space
        self.stochastic = stochastic

        if env_type == 'atari':
            policy = build_policy(env,'cnn')
        elif env_type == 'mujoco':
            policy = build_policy(env,'mlp')

        make_model = lambda : Model(policy=policy, ob_space=ob_space, ac_space=ac_space, nbatch_act=1, nbatch_train=1,
                        nsteps=1, ent_coef=0., vf_coef=0.,
                        max_grad_norm=0.)
        self.model = make_model()

    def load(self, path):
        self.model.load(path)

    def act(self, observation, reward, done):
        if self.stochastic:
            a,v,state,neglogp = self.model.step(observation)
        else:
            a = self.model.act_model.act(observation)
        return a

class PPO2Agent2(object):
    def __init__(self, env, env_type, stochastic=False, gpu=True):
        from baselines.common.policies import build_policy
        from baselines.ppo2.model import Model

        self.graph = tf.Graph()

        if gpu:
            config = tf.ConfigProto()
            config.gpu_options.allow_growth = True
        else:
            config = tf.ConfigProto(device_count = {'GPU': 0})

        self.sess = tf.Session(graph=self.graph,config=config)

        with self.graph.as_default():
            with self.sess.as_default():
                ob_space = env.observation_space
                ac_space = env.action_space

                if env_type == 'atari':
                    policy = build_policy(env,'cnn')
                elif env_type == 'mujoco':
                    policy = build_policy(env,'mlp')
                else:
                    assert False,' not supported env_type'

                make_model = lambda : Model(policy=policy, ob_space=ob_space, ac_space=ac_space, nbatch_act=1, nbatch_train=1,
                                nsteps=1, ent_coef=0., vf_coef=0.,
                                max_grad_norm=0.)
                self.model = make_model()


                #self.model.load(path)

        if env_type == 'mujoco':
            with open(path+'.env_stat.pkl', 'rb') as f :
                import pickle
                s = pickle.load(f)
            self.ob_rms = s['ob_rms']
            self.ret_rms = s['ret_rms']
            self.clipob = 10.
            self.epsilon = 1e-8
        else:
            self.ob_rms = None

        self.stochastic = stochastic

    def load(self, path):
        self.model_path = path
        self.model.load(path)

    def act(self, obs, reward, done):
        if self.ob_rms:
            obs = np.clip((obs - self.ob_rms.mean) / np.sqrt(self.ob_rms.var + self.epsilon), -self.clipob, self.clipob)

        with self.graph.as_default():
            with self.sess.as_default():
                if self.stochastic:
                    a,v,state,neglogp = self.model.step(obs)
                else:
                    a = self.model.act_model.act(obs)
        return a

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=None)
    parser.add_argument('--env_name', default='', help='Select the environment to run')
    parser.add_argument('--env_type', default='atari', help='mujoco or atari')
    parser.add_argument('--model_path', default='')
    parser.add_argument('--episode_count', default=100)
    parser.add_argument('--record_video', action='store_true')
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--stochastic', default=False, help="Should agent sample actions, i.e. explore", action='store_true')
    parser.add_argument('--random', action='store_true')
    parser.add_argument('--debug_crop', action='store_true')
    parser.add_argument('--no_op', action='store_true')
    args = parser.parse_args()

    env_name = args.env_name
    if env_name == "spaceinvaders":
        env_id = "SpaceInvadersNoFrameskip-v4"
    elif env_name == "mspacman":
        env_id = "MsPacmanNoFrameskip-v4"
    elif env_name == "videopinball":
        env_id = "VideoPinballNoFrameskip-v4"
    elif env_name == "beamrider":
        env_id = "BeamRiderNoFrameskip-v4"
    else:
        env_id = env_name[0].upper() + env_name[1:] + "NoFrameskip-v4"



    # You can set the level to logger.DEBUG or logger.WARN if you
    # want to change the amount of output.
    logger.set_level(logger.INFO)

    #env = gym.make(args.env_id)

    #env id, env type, num envs, and seed
    env = make_vec_env(env_id, args.env_type, 1, 0,
                       wrapper_kwargs={
                           'clip_rewards':False,
                           'episode_life':False,
                       })
    if args.record_video:
        env = VecVideoRecorder(env,'./videos/',lambda steps: True, 20000) # Always record every episode

    if args.env_type == 'atari':
        env = VecFrameStack(env, 4)
    elif args.env_type == 'mujoco':
        env = VecNormalize(env,ob=True,ret=False,eval=True)
    else:
        assert False, 'not supported env type'

    try:
        env.load(args.model_path) # Reload running mean & rewards if available
    except AttributeError:
        pass

    if args.random:
        agent = RandomAgent(env.unwrapped.envs[0].unwrapped.action_space)
    else:
        agent = PPO2Agent(env,args.env_type, args.stochastic)
        agent.load(args.model_path)
    #agent = RandomAgent(env.action_space)

    episode_count = args.episode_count
    reward = 0
    done = False

    for i in range(int(episode_count)):
        ob = env.reset()
        steps = 0
        acc_reward = 0
        while True:
            if args.no_op:
                action = 0
            else:
                action = agent.act(ob, reward, done)
            ob, reward, done, _ = env.step(action)
            if args.debug_crop:
                plt.figure()
                print(ob.shape)
                plt.imshow(ob[0])
                plt.title("before crop")
                plt.figure()
                ob = mask_score(ob, env_name)
                print(ob.shape)
                plt.imshow(ob[0])
                plt.title("after crop")
                plt.show()
            if args.render:
                env.render()

            steps += 1
            acc_reward += reward
            if done:
                print(steps,acc_reward)
                break

    env.close()
    env.venv.close()
